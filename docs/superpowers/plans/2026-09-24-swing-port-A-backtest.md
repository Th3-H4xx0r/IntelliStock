# Swing Port A-backtest: next-open fills, whole shares and bracket legs in the backtest simulator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach IntelliStock's equity backtest simulator three things the swing strategy needs: orders that fill at the next session's OPEN, whole-share quantities, and bracket legs (stop and target) that trigger off each later bar's high and low with one-cancels-other. A run that never asks for any of the three stays byte-identical.

**Architecture:** `SimulationOrder` gains `bracket`, `whole_shares` and `fill_at_next_open`. `NextEventExecutionSimulator.on_bar(SimulationBarEvent)` fills next-open orders at a bar's open and checks armed legs against its range. Legs live in `_bracket_legs`, outside `_pending`. `PortfolioEmulator.process_bar_events` applies those fills. broker.py calls it through `_process_backtest_bar_events` after the pending-fill block and before the strategy call, and only when the emulator has legs or next-open orders. A new import-safe module, `backend/backtest_bar_events.py`, gathers every bar a symbol still needs. It also turns a strategy's `_nexus_position_sizes[sym]` hint into `execute_signal` keywords, and only when the hint sets them.

**Tech Stack:** Python 3.14, pytest, `exchange_calendars` (through `live_calendar`), pandas. No database access.

**Spec:** `docs/superpowers/specs/2026-09-24-swing-trader-port-design.md`. This plan covers §6.2, the backtest half of §5.1 (items 4–5: the entry hint `{"buy_cash", "bracket", "whole_shares", "fill_at_next_open"}` and the exit hint `{"sell_fraction", "fill_at_next_open"}`), §11 (simulator and EB invariance tests), and the §12 deploy-check list for the files this plan touches.
**Shared contract:** `docs/superpowers/plans/2026-09-24-swing-port-interfaces.md`. Section 4 belongs to this plan. Every name there is used verbatim. The additions below must be copied into that section before, or together with, this plan's first commit.

## Contract additions (to be added to interfaces §4)

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

## Global Constraints

- **Byte-identical non-bracket backtests.** A run whose strategies never set `bracket`, `whole_shares` or `fill_at_next_open` produces identical fills, `execution_summary`, trade rows and portfolio history. Task 1's golden is the guard, and every later task re-runs it. Never regenerate the golden to make a task pass.
- **Isolation rule (spec §6):** a new branch runs only when an order carries one of the new fields. EB on doc 200 (alpaca-main, real money) must take byte-identical code paths.
- **DB:** nothing in this plan touches the datastore. If a step ever needs it, it uses `from db import store` only (CLAUDE.md).
- **Tests:** run from the repo root with `python3 -m pytest backend/tests/<file> -q -p no:cacheprovider`. Suite baseline: 7,565 passed and **exactly 19** pre-existing failures. The target is no new failures.
- **broker.py is not import-safe.** Test its functions by AST extraction, listing every extracted name and stubbing every global it reads (pattern: `backend/tests/test_strategy_x_broker_coexistence.py`, `_DISPATCHER_NAMES`).
- **GitNexus (CLAUDE.md):**
  - At the start of each task, run `mcp__gitnexus__impact` (`direction: "upstream"`, `repo: "IntelliStock"`) on every symbol the task modifies, and report the blast radius and risk in the task summary.
  - If the tool warns the index is stale, run `npx gitnexus analyze` first. On 2026-09-24 the index was 3 commits behind and resolved no callers for these symbols (risk UNKNOWN), so every task also gives a grep fallback.
  - broker.py is not indexed, so grep its callers.
  - If GitNexus returns HIGH or CRITICAL, warn the operator before editing.
  - Before each commit, run `mcp__gitnexus__detect_changes({scope: "staged", repo: "IntelliStock"})` and confirm that only the task's symbols changed.
- **Commits:**
  - Stage explicit paths only. Never `git add -A`, and never stage `AGENTS.md` or `CLAUDE.md` (both are modified in the working tree and are not ours).
  - Commit message bodies contain no backticks.
  - Every commit ends with exactly these two lines:
    ```
    Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
    Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
    ```
- **Scope:** backtest only. Live order paths (plan A-live) and strategy, data and UI code (plans B and C) are out of scope. `execution_hint_kwargs` is called only in the backtest branch of the main submit; live ignores `fill_at_next_open`.

## Review Focus

1. **A bracket parent the cash cannot fully pay for.** The open gaps up, so the budget buys fewer whole shares than were ordered. The legs must cover exactly the shares bought, and the rest is dropped and counted, not left pending. Pinned in Task 5 by `test_an_entry_the_cash_cannot_fully_pay_for_protects_what_it_bought`.
2. **An RSI exit and a stop on the same bar.** A next-open exit fills at the open, and the legs die with the position, as live cancels the legs before the exit reaches the auction. A leg that fires first cancels a pending close-filled strategy sell, with no crash and no double sale. Pinned in Task 5 by `test_an_rsi_exit_at_the_open_beats_a_stop_later_in_the_same_bar` and `test_a_stop_cancels_the_pending_strategy_sell_and_its_reservation`, and in Task 4 by `test_one_cancels_other_and_the_pending_strategy_sell_is_cancelled`.
3. **Bars missing for a symbol (a halt or a delisting) while legs are armed.** Ticks with no bar are no-ops. The legs stay armed, and the reopening bar's gap fills the stop at the open. Pinned in Task 6 by `test_a_halt_leaves_the_legs_armed_and_the_reopen_gap_is_honoured`, and in Task 7 by `test_the_bar_hook_is_a_no_op_while_a_symbol_has_no_new_bar`.
4. **A split-shaped price jump across a leg.** Bars are fetched `adjustment=split`, so a halving in the cache is a real move (or a stale cache to purge). The stop fills at the gapped open, with no split special-casing, matching `reconcile_splits` being default-OFF. Pinned in Task 4 by `test_a_split_shaped_gap_is_traded_as_a_gap`.
5. **Several bars becoming visible in one tick** (a holiday, a skipped tick). Every bar is replayed oldest first. The entry fills at the FIRST open after the decision, and the legs see each bar in order. Pinned in Task 6 by `test_bars_that_arrive_together_are_replayed_in_order` and `test_every_bar_since_the_requirement_arrives_oldest_first`, and in Task 3 by `test_a_bar_that_opened_before_the_decision_is_not_the_next_open`.

---

## Verified current code (HEAD 299d551, 2026-09-24)

Every location below was read before this plan was written. Each task quotes the exact text it replaces.

**Where the strategy runs and what it sees:**
- The backtest strategy call is `broker.py:15506-15514` (`run_run_once_strategies(..., price_history if mode == MODE_BACKTEST else None, ...)`), which reaches `instance.run_once` at `broker.py:7517-7535`.
- `price_history` comes from `get_price_history_up_to_current` (`broker.py:14511` → `12712-12725` → `backend/backtest_price_history.py:48-155`).
- A daily bar is visible once its NYSE session close is at or before the clock: `_equity_daily_bar_session_close` at `broker.py:12187-12200`, and `_backtest_bar_availability_resolver` at `12204-12219`.

**Fill timing today:**
- The execution delay is one step: `broker.py:10921` passes `execution_delay=backtest_increment_td`.
- `execute_not_before = timestamp + self._execution_delay` (`portfolio_emulator.py:1608`).
- The pending block quotes the latest visible CLOSE, at its availability time (`_get_price_events_at_time`, `broker.py:12267-12315`; the quote is built at `portfolio_emulator.py:1357-1363`).
- Eligibility is decided at `simulated_execution.py:671-683`.
- So a signal decided at tick D (which saw close D−1) fills at close(D+1).

**Simulator (`simulated_execution.py`):**
- `_pending` at 555; `on_quote` at 634-855: market pricing 727-736, passive branch 689-726, cash clamp 771-786, fee 788-791, passive zero costs 818-821.
- `SimulationOrder` at 277-346; `SimulationPriceEvent` at 364-380, carrying one price.
- `execution_summary` at 857-871, with `unfilled_order_count` at 865. It must be 0 for promotion (`backtest_summary.py:97-106`).

**Emulator (`portfolio_emulator.py`):**
- `apply_fill` at 1168-1260 (the trade row at 1244-1260).
- `process_quote` at 1262-1311; `pending_execution_symbols` at 1313-1316, read by EB's guard (`strategies/strategy_eb.py:187`) and by Strategy X (`strategies/strategy_x.py:440`).
- `execute_signal` at 1473-1654: buy quantity 1546-1548, sell reservations 1559-1568, order at 1601-1613.
- `_recorded_orders` (372, 1150) is never serialised.

**Broker plumbing:**
- The main backtest submit is `broker.py:19075-19084`: `_submit_portfolio_signal(..., order_source=_anchor_order_source)`. `nexus_hint` is in scope, from 17666-17668.
- `_submit_portfolio_signal` is at `broker.py:12354-12373`.
- The pending-fill block ends with `_reconcile_anchor_pending_orders(portfolio_emulator)` at `broker.py:14565`. The bar hook goes directly after it, before the strategy call at 15506.
- broker.py's main loop is module-level code, so helpers must be defined above it. `get_price_history_up_to_current` ends at 12725, just before `print("Time Increment:", time_increment)` at 12727.

**Cost models:** `LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL` (`simulated_execution.py:116-122`) and the substitution rule in `create_backtest_emulator` (`portfolio_emulator.py:1762-1784`). The leg costs reuse `self._model_for(symbol)`, so tiered runs price each leg at its tier.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `backend/simulated_execution.py` | Modify | Order, bar and fill types; next-open fills; legs, triggers, one-cancels-other; ordering priority; summary keys |
| `backend/portfolio_emulator.py` | Modify | Forward the three keywords to `SimulationOrder`; floor whole shares; `process_bar_events`; reservation bookkeeping shared with `process_quote`; `exit_reason` on trade rows |
| `backend/backtest_bar_events.py` | Create | Import-safe: hint → keywords, bar open times, collecting every bar a symbol still needs |
| `backend/broker.py` | Modify | Forward hints at the main backtest submit; the bar hook and its two helpers |
| `backend/api/main.py`, `scripts/check_deployed_code.py` | Modify | Add the three engine files to both fingerprint lists (spec §12) |
| `backend/tests/test_swing_port_invariance.py` + `backend/tests/fixtures/swing_port_non_bracket_golden.json` | Create | The byte-identical guard, written against pre-change code |
| `backend/tests/test_swing_port_sim_types.py` | Create | Type validation |
| `backend/tests/test_swing_port_next_open.py` | Create | Next-open fills |
| `backend/tests/test_swing_port_bracket_legs.py` | Create | Trigger rules, costs, one-cancels-other |
| `backend/tests/test_swing_port_emulator_bars.py` | Create | Emulator plumbing and ordering |
| `backend/tests/test_backtest_bar_events.py` | Create | Hints, open times, collector |
| `backend/tests/test_swing_port_backtest_scenarios.py` | Create | The broker's tick order on real NYSE sessions: halt, holiday |
| `backend/tests/test_swing_port_broker_wiring.py` | Create | AST-extracted broker helpers, source placement, fingerprint lists |

## Notes for plans B and A-live (hand-offs; not implemented here)

**For plan B (the swing strategy):**
- **Share count.** With `whole_shares`, the count is `floor(affordable_buy_quantity(buy_cash, prior_close))`: buy cash divided by the ALL-IN price (half spread + slippage + fee ≈ 23.2 bps by default). That can be one share fewer than ST's `int(equity × 0.125 / close)` when the quotient sits within about 0.23% of an integer.
- **Bracket exits happen between ticks.** The strategy learns about them from `portfolio_emulator.get_positions()` (the position is gone), and trade rows carry `exit_reason`. The min-hold floor and the turnover ledger do not see leg exits.
- **Same-open funding.** The lab document should set `backtest_credit_pending_sell_proceeds: true`. Otherwise an entry decided on the same tick as an exit is sized before the exit's proceeds exist.
- **The bracket hint must be a mapping with a positive stop below a positive target.** A malformed hint raises `ValueError` and ends the backtest loudly, by design.
- **Next-open symbols must be in the broker's `data` map (the watchlist).** An order for a symbol with no later bars stays pending and blocks promotion (`unfilled_order_count > 0`).

**For plan A-live:** `execution_hint_kwargs` is backtest-only. The live path builds its own bracket intent from the same hint (spec §6.1, item 3).

---

### Task 1: Pin a non-bracket backtest byte for byte (the invariance golden)

**Files:**
- Create: `backend/tests/test_swing_port_invariance.py`
- Create (generated by pre-change code): `backend/tests/fixtures/swing_port_non_bracket_golden.json`

**Interfaces:**
- Consumes: today's `PortfolioEmulator`, `NextEventExecutionSimulator`, `SimulationPriceEvent` and `tiered_cost_model`, unchanged.
- Produces: `_run(submit)`, `_submit_plain(emulator, day, now)`, `_canonical(result) -> str`, `GOLDEN` and `GOLDEN_SHA256`. Task 5 appends two tests to this file that reuse them.

- [ ] **Step 1: Record the suite baseline.** It takes several minutes; allow 20.

```bash
python3 -m pytest backend/tests -q -p no:cacheprovider 2>&1 | grep -E "^(FAILED|ERROR) " | sed 's/ - .*//' | sort > "${TMPDIR:-/tmp}/swing-port-A-baseline-failures.txt"
wc -l < "${TMPDIR:-/tmp}/swing-port-A-baseline-failures.txt"
```

Expected: `19`. If it is not 19, stop and report the difference before changing anything.

- [ ] **Step 2: Impact analysis.** This task only adds a test, so there are no symbols to analyse. Record "no symbols modified" in the task summary.

- [ ] **Step 3: Write the characterization test**

Create `backend/tests/test_swing_port_invariance.py`:

```python
"""A backtest that never asks for a bracket or a next-open fill is unchanged.

Spec 2026-09-24-swing-trader-port-design.md, sections 6 and 11: every new
simulator branch triggers only for an order that carries `bracket`,
`whole_shares` or `fill_at_next_open`. This file pins that promise against the
code as it stood BEFORE any of that work: a deterministic non-bracket run --
two symbols, a tiered cost model, a cash-clamped buy, a sub-$1 reject, partial
and full sells, orders still pending at the end -- serialised byte for byte.

The golden was written by the pre-change code (plan Task 1). Never regenerate
it to make a later task pass: a diff here IS the regression.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from portfolio_emulator import PortfolioEmulator  # noqa: E402
from simulated_execution import (  # noqa: E402
    LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL,
    NextEventExecutionSimulator,
    SimulationPriceEvent,
    tiered_cost_model,
)

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "swing_port_non_bracket_golden.json"
DAY = timedelta(days=1)
#: 05:00 PT on a weekday, as the broker's naive-UTC clock carries it.
T0 = datetime(2026, 3, 2, 13, 0)
CLOSES = {
    "SPY": [500.0, 505.0, 498.0, 510.0, 507.0, 512.0, 509.0, 515.0],
    "ABC": [20.0, 19.5, 21.0, 22.5, 18.0, 18.5, 19.25, 20.0],
}


def _close_event(symbol, day):
    """Day `day`'s close, visible from 21:00 UTC that day."""
    close_at = datetime(2026, 3, 2, 21, 0, tzinfo=timezone.utc) + day * DAY
    return SimulationPriceEvent(
        symbol=symbol,
        price=CLOSES[symbol][day],
        available_at=close_at,
        bar_timestamp=close_at - timedelta(hours=16),
    )


def _run(submit):
    """Drive one emulator through eight daily ticks. `submit(emu, day, now)`
    places that tick's orders, so a later test can replay the same run through
    a different submission path."""
    simulator = NextEventExecutionSimulator(
        tiered_cost_model("etf-liquid", LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL))
    emulator = PortfolioEmulator(
        10_000.0, execution_simulator=simulator, execution_delay=DAY)
    steps = []
    for day in range(len(CLOSES["SPY"])):
        now = T0 + day * DAY
        visible = {}
        if day:
            events = {s: _close_event(s, day - 1) for s in CLOSES}
            emulator.process_price_events(events)
            visible = {s: CLOSES[s][day - 1] for s in CLOSES}
        emulator.save_portfolio_snapshot(visible, timestamp=now)
        submit(emulator, day, now)
        steps.append({
            "day": day,
            "cash": emulator.get_cash(),
            "buying_power": emulator.get_buying_power(),
            "positions": emulator.get_positions(),
            "pending": list(emulator.pending_execution_symbols()),
            "pending_sell_proceeds": emulator.pending_sell_proceeds(),
        })
    return {
        "steps": steps,
        "trades": emulator.get_trade_history(),
        "summary": emulator.get_execution_summary(),
        "portfolio": emulator.get_portfolio_history(),
    }


def _submit_plain(emulator, day, now):
    if day == 0:
        emulator.execute_signal("SPY", 1, 500.0, timestamp=now,
                                cash_per_trade=4000.0, order_source="main_signal")
        # More than the account can fund: clamped to buying power.
        emulator.execute_signal("ABC", 1, 20.0, timestamp=now,
                                cash_per_trade=7000.0, order_source="main_signal")
    elif day == 2:
        # Under Alpaca's $1 minimum: refused and counted, never submitted.
        emulator.execute_signal("SPY", 1, 505.0, timestamp=now,
                                cash_per_trade=0.5, order_source="main_signal")
    elif day == 3:
        emulator.execute_signal("ABC", -1, 22.5, timestamp=now,
                                sell_fraction=0.5, order_source="main_signal")
    elif day == 4:
        emulator.execute_signal("SPY", -1, 507.0, timestamp=now,
                                sell_fraction=1.0, order_source="main_signal")
    elif day == 6:
        emulator.execute_signal("ABC", 1, 19.25, timestamp=now,
                                cash_per_trade=300.0, order_source="main_signal")
        emulator.execute_signal("ABC", -1, 19.25, timestamp=now,
                                sell_fraction=1.0, order_source="main_signal")


def _canonical(result) -> str:
    return json.dumps(result, indent=1, default=str) + "\n"


def test_non_bracket_run_is_byte_identical_to_the_pre_change_golden():
    assert _canonical(_run(_submit_plain)) == GOLDEN.read_text()


def test_the_golden_itself_was_not_regenerated():
    digest = hashlib.sha256(GOLDEN.read_bytes()).hexdigest()
    assert digest == GOLDEN_SHA256, (
        "the golden changed. It may only be written once, by the pre-change "
        "code; restore it from git instead of regenerating it.")


GOLDEN_SHA256 = "9731cf219e9e0f6405a9e5ffdea3dc5244218015a60c9f448872d099f4b6a182"


if __name__ == "__main__":
    if sys.argv[1:] == ["--write-golden"]:
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(_canonical(_run(_submit_plain)))
        print(hashlib.sha256(GOLDEN.read_bytes()).hexdigest())
```

- [ ] **Step 4: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_port_invariance.py -q -p no:cacheprovider`
Expected: 2 FAIL, with `FileNotFoundError: ... swing_port_non_bracket_golden.json`.

- [ ] **Step 5: Write the golden with the pre-change code**

Run: `python3 backend/tests/test_swing_port_invariance.py --write-golden`
Expected output: `9731cf219e9e0f6405a9e5ffdea3dc5244218015a60c9f448872d099f4b6a182`.

That value was produced on 2026-09-24 against HEAD 299d551 with Python 3.14.5. The file contains 4 trades, `unfilled_order_count` 2 and `sub_minimum_rejected_order_count` 1. If your run prints a different hash, the pre-change behaviour differs from the plan's baseline. The golden is still valid, because it only has to be written by pre-change code: put the printed hash in `GOLDEN_SHA256` and say so in the task summary.

- [ ] **Step 6: Run it to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_port_invariance.py -q -p no:cacheprovider`
Expected: `2 passed`.

- [ ] **Step 7: Check scope, then commit**

```bash
git add backend/tests/test_swing_port_invariance.py backend/tests/fixtures/swing_port_non_bracket_golden.json
```

Run `mcp__gitnexus__detect_changes({scope: "staged", repo: "IntelliStock"})`. Expected: two new test files and no changed production symbols.

```bash
git commit -F - <<'EOF'
test(backtest): pin a non-bracket run byte for byte before the swing-port simulator work

Writes a golden of fills, execution summary, trade rows and portfolio
history for a deterministic non-bracket emulator run, using the code as
it stands before next-open fills and bracket legs exist. Every later task
re-runs it; the SHA-256 pin stops anyone regenerating it to pass.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 2: Order, bar and fill types

**Files:**
- Modify: `backend/simulated_execution.py`: `SimulationOrder` (277-346), a new `SimulationBarEvent` after it, and `SimulationFill` (433-522)
- Test: `backend/tests/test_swing_port_sim_types.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `SimulationOrder(..., bracket: dict | None = None, whole_shares: bool = False, fill_at_next_open: bool = False)`. `bracket` is normalised to `{"take_profit_price": float, "stop_loss_price": float}`.
  - `SimulationBarEvent(symbol, open, high, low, close, bar_ts, available_at)` (frozen).
  - `SimulationFill(..., exit_reason: str | None = None)`, whose `as_dict()` omits `exit_reason` when it is `None`.

- [ ] **Step 1: Impact analysis.** Run `mcp__gitnexus__impact` upstream on `SimulationOrder` and `SimulationFill` (file `backend/simulated_execution.py`). Grep fallback:

```bash
grep -rn "SimulationOrder(\|SimulationFill(" backend --include='*.py' | grep -v /tests/
```

Expected today: one production `SimulationOrder(` (in `portfolio_emulator.py`, `execute_signal`) and one `SimulationFill(` (in `simulated_execution.py`, `on_quote`). Both are on every equity backtest's fill path. The new fields default to today's behaviour, and Task 1's golden guards them. Report that in the task summary.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_swing_port_sim_types.py`:

```python
"""The swing-port order, bar and fill types (spec 6.2, interfaces section 4)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from simulated_execution import (  # noqa: E402
    SimulationBarEvent,
    SimulationFill,
    SimulationOrder,
)

T0 = datetime(2026, 3, 2, 13, 0, tzinfo=timezone.utc)
OPEN = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)
CLOSE = datetime(2026, 3, 2, 21, 0, tzinfo=timezone.utc)


def _order(**overrides):
    values = dict(order_id="o1", symbol="aapl", side="buy", quantity=10.0,
                  decision_at=T0, execute_not_before=T0, source="main_signal")
    values.update(overrides)
    return SimulationOrder(**values)


def test_new_order_fields_default_to_todays_behaviour():
    order = _order()
    assert order.bracket is None
    assert order.whole_shares is False
    assert order.fill_at_next_open is False


def test_bracket_is_normalised_to_two_float_prices():
    order = _order(bracket={"take_profit_price": "218", "stop_loss_price": 188,
                            "ignored": 1})
    assert order.bracket == {"take_profit_price": 218.0,
                             "stop_loss_price": 188.0}


@pytest.mark.parametrize("bracket, message", [
    ({"take_profit_price": 188.0, "stop_loss_price": 218.0}, "below"),
    ({"take_profit_price": 200.0, "stop_loss_price": 200.0}, "below"),
    ({"take_profit_price": 218.0}, "stop_loss_price"),
    ({"take_profit_price": float("nan"), "stop_loss_price": 1.0}, "finite"),
    ({"take_profit_price": 218.0, "stop_loss_price": -1.0}, "positive"),
    ([218.0, 188.0], "mapping"),
])
def test_bad_brackets_are_refused(bracket, message):
    with pytest.raises(ValueError, match=message):
        _order(bracket=bracket)


def test_a_sell_cannot_carry_a_bracket():
    with pytest.raises(ValueError, match="buy orders"):
        _order(side="sell", bracket={"take_profit_price": 2.0,
                                     "stop_loss_price": 1.0})


def test_whole_shares_needs_a_whole_quantity():
    assert _order(quantity=7.0, whole_shares=True).whole_shares is True
    with pytest.raises(ValueError, match="whole-share"):
        _order(quantity=7.5, whole_shares=True)


def test_a_next_open_order_is_a_market_order():
    assert _order(fill_at_next_open=True).fill_at_next_open is True
    with pytest.raises(ValueError, match="market orders"):
        _order(fill_at_next_open=True, limit_price=100.0)


def test_bar_event_normalises_and_validates():
    event = SimulationBarEvent(symbol=" aapl ", open="100", high=105.0,
                               low=98.0, close=101.0, bar_ts=OPEN,
                               available_at=CLOSE)
    assert event.symbol == "AAPL"
    assert event.open == 100.0
    with pytest.raises(ValueError, match="high cannot be below low"):
        SimulationBarEvent(symbol="AAPL", open=100.0, high=97.0, low=98.0,
                           close=99.0, bar_ts=OPEN, available_at=CLOSE)
    with pytest.raises(ValueError, match="available_at cannot precede"):
        SimulationBarEvent(symbol="AAPL", open=100.0, high=105.0, low=98.0,
                           close=101.0, bar_ts=CLOSE, available_at=OPEN)
    with pytest.raises(ValueError, match="positive"):
        SimulationBarEvent(symbol="AAPL", open=0.0, high=105.0, low=98.0,
                           close=101.0, bar_ts=OPEN, available_at=CLOSE)


def _fill(**overrides):
    values = dict(order_id="o1", symbol="AAPL", side="sell",
                  incremental_quantity=5.0, cumulative_quantity=5.0,
                  price=100.0, fees=0.1, spread_cost=0.0, slippage_cost=0.0,
                  quote_timestamp=OPEN, executed_at=OPEN,
                  cost_model_version="v", source="main_signal")
    values.update(overrides)
    return SimulationFill(**values)


def test_a_plain_fill_serialises_exactly_as_before():
    """No `exit_reason` key at all: fill_provenance of every non-bracket run
    stays byte-identical."""
    assert "exit_reason" not in _fill().as_dict()
    assert list(_fill().as_dict()) == [
        "order_id", "symbol", "side", "incremental_quantity",
        "cumulative_quantity", "price", "fees", "spread_cost",
        "slippage_cost", "quote_timestamp", "executed_at",
        "cost_model_version", "source", "order_quantity", "is_final",
    ]


def test_a_bracket_fill_carries_its_exit_reason():
    fill = _fill(exit_reason=" stop_loss ", source="bracket_sl:o0")
    assert fill.exit_reason == "stop_loss"
    assert fill.as_dict()["exit_reason"] == "stop_loss"
    with pytest.raises(ValueError, match="exit_reason"):
        _fill(exit_reason="  ")
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_port_sim_types.py -q -p no:cacheprovider`
Expected: collection error, `ImportError: cannot import name 'SimulationBarEvent' from 'simulated_execution'`.

- [ ] **Step 4: Implement the order fields**

In `backend/simulated_execution.py`, replace:

```python
    expire_after_quotes: int = 0

    def __post_init__(self) -> None:
```

with:

```python
    expire_after_quotes: int = 0
    #: 2026-09-24 swing port (spec 6.2). All three default to today's
    #: behaviour, and nothing below reads them unless an order sets one.
    #:
    #: `bracket` -- {"take_profit_price", "stop_loss_price"}: ABSOLUTE prices,
    #: the same numbers a live Alpaca bracket carries. When this BUY fills, the
    #: simulator arms a stop leg and a target leg for the filled quantity and
    #: checks them against every later bar's high and low (`on_bar`).
    bracket: dict | None = None
    #: The quantity is a whole number of shares, and every clamp that could cut
    #: it (the cash budget) floors to a whole share rather than a fraction.
    whole_shares: bool = False
    #: Fill at the OPEN of the first bar whose session starts after
    #: `decision_at`, not at a later close. Filled only by `on_bar`; `on_quote`
    #: skips it. One shot: whatever is unfilled after that bar is dropped.
    fill_at_next_open: bool = False

    def __post_init__(self) -> None:
```

- [ ] **Step 5: Implement their validation and `SimulationBarEvent`**

In `backend/simulated_execution.py`, replace:

```python
        if expire < 0:
            raise ValueError("expire_after_quotes cannot be negative")
        object.__setattr__(self, "expire_after_quotes", expire)
```

with:

```python
        if expire < 0:
            raise ValueError("expire_after_quotes cannot be negative")
        object.__setattr__(self, "expire_after_quotes", expire)
        bracket = self.bracket
        if bracket is not None:
            if side != "buy":
                raise ValueError("bracket is only valid for buy orders")
            if not isinstance(bracket, dict):
                raise ValueError("bracket must be a mapping")
            take_profit = _finite_number(
                bracket.get("take_profit_price"),
                field="bracket.take_profit_price", positive=True)
            stop_loss = _finite_number(
                bracket.get("stop_loss_price"),
                field="bracket.stop_loss_price", positive=True)
            if not stop_loss < take_profit:
                raise ValueError(
                    "bracket stop_loss_price must be below take_profit_price")
            bracket = {"take_profit_price": take_profit,
                       "stop_loss_price": stop_loss}
        object.__setattr__(self, "bracket", bracket)
        whole_shares = bool(self.whole_shares)
        if whole_shares and not float(quantity).is_integer():
            raise ValueError("whole_shares orders need a whole-share quantity")
        object.__setattr__(self, "whole_shares", whole_shares)
        fill_at_next_open = bool(self.fill_at_next_open)
        if fill_at_next_open and limit_price is not None:
            raise ValueError("fill_at_next_open orders are market orders")
        object.__setattr__(self, "fill_at_next_open", fill_at_next_open)


@dataclass(frozen=True)
class SimulationBarEvent:
    """One completed bar, for next-open fills and bracket legs.

    `bar_ts` is the instant the bar's first trade could print: the NYSE
    session open for a daily bar, the bar's own label for an intraday one.
    `available_at` is when the whole bar is known (the session close for a
    daily bar). Both are what `backtest_bar_events.collect_bar_events` builds.
    """

    symbol: str
    open: float
    high: float
    low: float
    close: float
    bar_ts: datetime
    available_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        for field in ("open", "high", "low", "close"):
            object.__setattr__(
                self, field,
                _finite_number(getattr(self, field), field=field,
                               positive=True))
        if self.high < self.low:
            raise ValueError("high cannot be below low")
        opened = _event_seconds(self.bar_ts, field="bar_ts")
        known = _event_seconds(self.available_at, field="available_at")
        if known < opened:
            raise ValueError("available_at cannot precede bar_ts")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
```

(`quantity` and `limit_price` are the locals `__post_init__` already validated above this point.)

- [ ] **Step 6: Implement `SimulationFill.exit_reason`**

In `backend/simulated_execution.py`, replace:

```python
    order_quantity: float | None = None
    is_final: bool = False
```

with:

```python
    order_quantity: float | None = None
    is_final: bool = False
    #: "stop_loss" | "take_profit" on a bracket-leg fill; None on every other
    #: fill, and then absent from `as_dict()` so fill provenance is unchanged.
    exit_reason: str | None = None
```

In `backend/simulated_execution.py`, replace:

```python
        object.__setattr__(self, "is_final", bool(self.is_final))

    def as_dict(self) -> dict:
        result = asdict(self)
        result["quote_timestamp"] = self.quote_timestamp.isoformat()
        result["executed_at"] = self.executed_at.isoformat()
        return result
```

with:

```python
        object.__setattr__(self, "is_final", bool(self.is_final))
        exit_reason = self.exit_reason
        if exit_reason is not None:
            exit_reason = str(exit_reason).strip()
            if not exit_reason:
                raise ValueError("exit_reason must be a non-empty string")
        object.__setattr__(self, "exit_reason", exit_reason)

    def as_dict(self) -> dict:
        result = asdict(self)
        result["quote_timestamp"] = self.quote_timestamp.isoformat()
        result["executed_at"] = self.executed_at.isoformat()
        if result.get("exit_reason") is None:
            result.pop("exit_reason", None)
        return result
```

- [ ] **Step 7: Run the new tests, the guard and the existing simulator tests**

Run: `python3 -m pytest backend/tests/test_swing_port_sim_types.py backend/tests/test_swing_port_invariance.py backend/tests/test_simulated_execution.py backend/tests/test_passive_limit_execution.py backend/tests/test_backtest_execution_costs.py -q -p no:cacheprovider`
Expected: all pass (14 new, 2 invariance).

- [ ] **Step 8: Check scope, then commit**

```bash
git add backend/simulated_execution.py backend/tests/test_swing_port_sim_types.py
```

Run `mcp__gitnexus__detect_changes({scope: "staged", repo: "IntelliStock"})`. Expected changed symbols: `SimulationOrder`, `SimulationOrder.__post_init__`, `SimulationBarEvent` (new), `SimulationFill`, `SimulationFill.__post_init__`, `SimulationFill.as_dict`.

```bash
git commit -F - <<'EOF'
feat(backtest): order, bar and fill types for next-open fills and brackets

SimulationOrder gains bracket, whole_shares and fill_at_next_open, all
defaulting to today's behaviour and validated only when set. The new
SimulationBarEvent carries one completed bar with its open and
availability times. SimulationFill gains exit_reason, omitted from as_dict
when unset so the fill provenance of every other run is unchanged.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 3: Next-open fills in the simulator, and whole-share clamps

**Files:**
- Modify: `backend/simulated_execution.py`: `NextEventExecutionSimulator.__init__` (547-560), `submit` (600-608), `on_quote` (634-855) and `execution_summary` (857-871); new `has_next_open_orders`, `bar_event_requirements`, `_due_next_open` and `on_bar`
- Test: `backend/tests/test_swing_port_next_open.py`

**Interfaces:**
- Consumes: from Task 2, `SimulationOrder.fill_at_next_open` and `.whole_shares`, and `SimulationBarEvent`.
- Produces:
  - `NextEventExecutionSimulator.on_bar(event, *, accept_fill=None, cash_budget=None, position_of=None) -> list[SimulationFill]`
  - `.has_next_open_orders` (property, bool)
  - `.bar_event_requirements() -> dict[str, datetime]` (aware UTC)
  - `._due_next_open(order, bar_seconds) -> bool` (staticmethod)
  - `on_quote(..., _next_open_only=False)`
  - summary keys `next_open_order_count` and `next_open_expired_order_count`

- [ ] **Step 1: Impact analysis.** Run `mcp__gitnexus__impact` upstream on `NextEventExecutionSimulator.on_quote`, `.submit` and `.execution_summary`. Grep fallback:

```bash
grep -rn "\.on_quote(\|\.execution_summary()\|_execution_simulator.submit(" backend --include='*.py' | grep -v /tests/
```

Expected today:
- `on_quote`: 1 production caller (`PortfolioEmulator.process_quote`), reached through `process_price_events`, which the broker's pending-fill block calls on every equity backtest tick.
- `execution_summary`: 1 caller (`get_execution_summary` → `backtest_summary.py:412`).

Report these as HIGH blast radius, guarded by Task 1.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_swing_port_next_open.py`:

```python
"""`fill_at_next_open` orders fill at the next session's OPEN (spec 6.2)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from simulated_execution import (  # noqa: E402
    ExecutionCostModel,
    NextEventExecutionSimulator,
    SimulationBarEvent,
    SimulationOrder,
    SimulationQuote,
)

COSTS = ExecutionCostModel(version="test-v1", spread_bps=20.0,
                           slippage_bps=10.0, fee_bps=5.0,
                           latency=timedelta(0))
#: Decision at 05:00 PT Monday (naive UTC, as the broker clock carries it).
DECISION = datetime(2026, 3, 2, 13, 0)
MON_OPEN = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)
MON_CLOSE = datetime(2026, 3, 2, 21, 0, tzinfo=timezone.utc)
TUE_OPEN = MON_OPEN + timedelta(days=1)
TUE_CLOSE = MON_CLOSE + timedelta(days=1)


def _order(**overrides):
    values = dict(order_id="o1", symbol="AAPL", side="buy", quantity=10.0,
                  decision_at=DECISION, execute_not_before=DECISION,
                  source="main_signal", fill_at_next_open=True)
    values.update(overrides)
    return SimulationOrder(**values)


def _bar(opened=MON_OPEN, known=MON_CLOSE, o=100.0, h=104.0, l=97.0, c=102.0,
         symbol="AAPL"):
    return SimulationBarEvent(symbol=symbol, open=o, high=h, low=l, close=c,
                              bar_ts=opened, available_at=known)


def test_an_ordinary_quote_never_fills_a_next_open_order():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order())
    quote = SimulationQuote.from_mid(symbol="AAPL", timestamp=MON_CLOSE,
                                     mid=102.0, spread_bps=20.0)
    assert sim.on_quote(quote) == ()
    assert sim.pending_order_count == 1


def test_a_buy_fills_at_the_open_with_the_market_cost_model():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order())
    [fill] = sim.on_bar(_bar(o=100.0))
    touch = 100.0 * (1 + 20.0 / 20_000)
    assert fill.price == pytest.approx(touch * (1 + 10.0 / 10_000))
    assert fill.spread_cost == pytest.approx((touch - 100.0) * 10)
    assert fill.fees == pytest.approx(10 * fill.price * 5.0 / 10_000)
    # Stamped at the bar's open -- not its close, not the tick's clock.
    assert fill.quote_timestamp == MON_OPEN
    assert fill.executed_at == MON_OPEN
    assert fill.source == "main_signal"
    assert sim.pending_order_count == 0


def test_a_sell_fills_at_the_open_below_it():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order(side="sell", quantity=4.0))
    [fill] = sim.on_bar(_bar(o=100.0))
    touch = 100.0 * (1 - 20.0 / 20_000)
    assert fill.price == pytest.approx(touch * (1 - 10.0 / 10_000))


def test_a_bar_that_opened_before_the_decision_is_not_the_next_open():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order(decision_at=datetime(2026, 3, 2, 15, 0),
                      execute_not_before=datetime(2026, 3, 2, 15, 0)))
    assert sim.on_bar(_bar()) == []            # Monday opened at 14:30
    assert sim.pending_order_count == 1
    [fill] = sim.on_bar(_bar(opened=TUE_OPEN, known=TUE_CLOSE, o=103.0))
    assert fill.quote_timestamp == TUE_OPEN


def test_one_shot_an_unfunded_order_is_dropped_and_counted_not_left_pending():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order())
    assert sim.on_bar(_bar(), cash_budget=lambda: 0.0) == []
    assert sim.pending_order_count == 0
    summary = sim.execution_summary()
    assert summary["unfilled_order_count"] == 0
    assert summary["next_open_order_count"] == 1
    assert summary["next_open_expired_order_count"] == 1
    # It never fills at a later, different open.
    assert sim.on_bar(_bar(opened=TUE_OPEN, known=TUE_CLOSE)) == []


def test_a_cash_clamp_floors_a_whole_share_order_and_the_rest_expires():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order(whole_shares=True))
    [fill] = sim.on_bar(_bar(o=100.0), cash_budget=lambda: 750.0)
    assert fill.incremental_quantity == 7.0          # 7.48 affordable -> 7
    assert fill.is_final is False
    assert sim.pending_order_count == 0
    assert sim.execution_summary()["next_open_expired_order_count"] == 1


def test_a_fractional_order_is_clamped_to_the_cent_as_before():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order())
    [fill] = sim.on_bar(_bar(o=100.0), cash_budget=lambda: 750.0)
    assert 7.4 < fill.incremental_quantity < 7.5


def test_a_processed_bar_is_skipped_when_sent_again():
    """Pins the per-symbol cursor. The broker never dates an order before a
    bar it has already processed; the second order here does so only to
    prove a resent bar is ignored."""
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order())
    assert len(sim.on_bar(_bar())) == 1
    sim.submit(_order(order_id="o2", decision_at=DECISION - timedelta(days=3),
                      execute_not_before=DECISION - timedelta(days=3)))
    assert sim.on_bar(_bar()) == []
    assert sim.pending_order_count == 1


def test_requirements_name_the_decision_then_the_last_bar_seen():
    sim = NextEventExecutionSimulator(COSTS)
    assert sim.bar_event_requirements() == {}
    sim.submit(_order())
    assert sim.has_next_open_orders is True
    assert sim.bar_event_requirements() == {
        "AAPL": DECISION.replace(tzinfo=timezone.utc)}
    sim.on_bar(_bar())
    assert sim.has_next_open_orders is False
    sim.submit(_order(order_id="o2", decision_at=DECISION - timedelta(days=3),
                      execute_not_before=DECISION - timedelta(days=3)))
    assert sim.bar_event_requirements() == {"AAPL": MON_OPEN}


def test_a_run_without_next_open_orders_has_no_new_summary_keys():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_order(fill_at_next_open=False,
                      execute_not_before=DECISION + timedelta(days=1)))
    summary = sim.execution_summary()
    assert "next_open_order_count" not in summary
    assert "next_open_expired_order_count" not in summary
    assert "bracket_order_count" not in summary
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_port_next_open.py -q -p no:cacheprovider`
Expected: FAIL. `test_an_ordinary_quote_never_fills_a_next_open_order` fails with `assert (SimulationFill(...),) == ()`, and the others fail with `AttributeError: 'NextEventExecutionSimulator' object has no attribute 'on_bar'` (or `'bar_event_requirements'`).

- [ ] **Step 4: Add the simulator's state**

In `backend/simulated_execution.py`, replace:

```python
        self._expired_order_count = 0
        self._refused_fill_count = 0
```

with:

```python
        self._expired_order_count = 0
        self._refused_fill_count = 0
        # Swing port (spec 6.2). Empty/zero for every run that never submits a
        # bracket or a next-open order, and nothing reads them then.
        self._bar_cursor: dict[str, float] = {}
        self._next_open_order_count = 0
        self._next_open_expired_count = 0
```

- [ ] **Step 5: Count next-open submissions, and expose the requirements**

In `backend/simulated_execution.py`, replace:

```python
        self._known_order_ids.add(order.order_id)
        self._pending[order.order_id] = _PendingOrder(order=order)
```

with:

```python
        self._known_order_ids.add(order.order_id)
        self._pending[order.order_id] = _PendingOrder(order=order)
        if order.fill_at_next_open:
            self._next_open_order_count += 1

    @property
    def has_next_open_orders(self) -> bool:
        return any(
            state.order.fill_at_next_open for state in self._pending.values())

    def bar_event_requirements(self) -> dict[str, datetime]:
        """{symbol: the earliest bar_ts `on_bar` still needs}, aware UTC.

        A next-open order needs bars after its decision; a leg needs bars from
        the one it was armed in. Raised to the last bar already processed for
        the symbol, which `on_bar` then skips, so the caller never has to know
        what has been seen.
        """
        needs: dict[str, float] = {}

        def _need(symbol, when):
            seconds = _event_seconds(when, field="requirement")
            if symbol not in needs or seconds < needs[symbol]:
                needs[symbol] = seconds

        for state in self._pending.values():
            if state.order.fill_at_next_open:
                _need(state.order.symbol, state.order.decision_at)
        return {
            symbol: datetime.fromtimestamp(
                max(seconds, self._bar_cursor.get(symbol, seconds)),
                tz=timezone.utc)
            for symbol, seconds in needs.items()
        }
```

- [ ] **Step 6: Teach `on_quote` to skip next-open orders and floor whole shares**

In `backend/simulated_execution.py`, replace:

```python
        accept_fill=None,
        cash_budget=None,
    ) -> tuple[SimulationFill, ...]:
        """Propose, account, then commit each fill.

        When ``accept_fill`` is supplied it runs before simulator state changes.
        If accounting rejects the candidate, the order remains pending and no
        fill provenance is recorded.
        """
```

with:

```python
        accept_fill=None,
        cash_budget=None,
        _next_open_only: bool = False,
    ) -> tuple[SimulationFill, ...]:
        """Propose, account, then commit each fill.

        When ``accept_fill`` is supplied it runs before simulator state changes.
        If accounting rejects the candidate, the order remains pending and no
        fill provenance is recorded.

        ``_next_open_only`` is `on_bar`'s: a quote built from a bar's OPEN
        fills only `fill_at_next_open` orders, and every ordinary quote skips
        them.
        """
```

In `backend/simulated_execution.py`, replace:

```python
            if order.symbol != quote.symbol or liquidity <= 0:
                continue
```

with:

```python
            if order.symbol != quote.symbol or liquidity <= 0:
                continue
            if order.fill_at_next_open != _next_open_only:
                continue
```

In `backend/simulated_execution.py`, replace:

```python
                    if affordable < incremental:
                        incremental, budget_bound = affordable, True
```

with:

```python
                    if affordable < incremental:
                        incremental, budget_bound = affordable, True
            if order.whole_shares:
                # A clamp may cut a whole-share order, never split a share.
                incremental = float(math.floor(incremental + 1e-9))
```

- [ ] **Step 7: Add `on_bar` (the next-open half)**

In `backend/simulated_execution.py`, replace:

```python
        for order_id in completed:
            self._pending.pop(order_id, None)
        return tuple(emitted)
```

with:

```python
        for order_id in completed:
            self._pending.pop(order_id, None)
        return tuple(emitted)

    # -- swing port: next-open fills and bracket legs (spec 6.2) -------------

    @staticmethod
    def _due_next_open(order, bar_seconds) -> bool:
        return (
            order.fill_at_next_open
            and bar_seconds > _event_seconds(
                order.decision_at, field="decision_at")
            and bar_seconds >= _event_seconds(
                order.execute_not_before, field="execute_not_before")
        )

    def on_bar(
        self,
        event: SimulationBarEvent,
        *,
        accept_fill=None,
        cash_budget=None,
        position_of=None,
    ) -> list[SimulationFill]:
        """Next-open fills, then bracket legs, for ONE completed bar.

        Bars must arrive oldest first per symbol; one already processed is
        skipped, so a caller may resend it. ``position_of(symbol)`` caps a leg
        at the shares actually held.
        """
        if not isinstance(event, SimulationBarEvent):
            raise ValueError("event must be a SimulationBarEvent")
        bar_seconds = _event_seconds(event.bar_ts, field="bar_ts")
        last = self._bar_cursor.get(event.symbol)
        if last is not None and bar_seconds <= last:
            return []
        emitted: list[SimulationFill] = []

        due = [
            order_id for order_id, state in self._pending.items()
            if state.order.symbol == event.symbol
            and self._due_next_open(state.order, bar_seconds)
        ]
        if due:
            quote = SimulationQuote.from_mid(
                symbol=event.symbol,
                timestamp=event.bar_ts,
                mid=event.open,
                spread_bps=self._model_for(event.symbol).spread_bps,
            )
            emitted.extend(self.on_quote(
                quote, accept_fill=accept_fill, cash_budget=cash_budget,
                _next_open_only=True))
            # One shot. Whatever this open did not fill is dropped and
            # counted, never left to fill at a later, different open.
            for order_id in due:
                if self._pending.pop(order_id, None) is not None:
                    self._next_open_expired_count += 1

        self._bar_cursor[event.symbol] = bar_seconds
        return emitted
```

- [ ] **Step 8: Add the next-open summary keys, only when used**

In `backend/simulated_execution.py`, replace:

```python
    def execution_summary(self) -> dict:
        return {
            "execution_provenance_complete": True,
```

with:

```python
    def execution_summary(self) -> dict:
        summary = {
            "execution_provenance_complete": True,
```

In `backend/simulated_execution.py`, replace:

```python
            "fill_provenance": [
                fill.as_dict() for fill in self._fills
            ],
        }
```

with:

```python
            "fill_provenance": [
                fill.as_dict() for fill in self._fills
            ],
        }
        # Swing-port keys appear only on a run that used the feature, so every
        # other run's summary is byte-identical.
        if self._next_open_order_count:
            summary["next_open_order_count"] = self._next_open_order_count
            summary["next_open_expired_order_count"] = (
                self._next_open_expired_count)
        return summary
```

- [ ] **Step 9: Run the new tests, the guard and the existing tests**

Run: `python3 -m pytest backend/tests/test_swing_port_next_open.py backend/tests/test_swing_port_sim_types.py backend/tests/test_swing_port_invariance.py backend/tests/test_simulated_execution.py backend/tests/test_passive_limit_execution.py backend/tests/test_portfolio_emulator_fills.py backend/tests/test_fill_never_exceeds_spendable_cash.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 10: Check scope, then commit**

```bash
git add backend/simulated_execution.py backend/tests/test_swing_port_next_open.py
```

Run `mcp__gitnexus__detect_changes({scope: "staged", repo: "IntelliStock"})`. Expected changed symbols: `NextEventExecutionSimulator.__init__`, `.submit`, `.on_quote` and `.execution_summary`, plus the new `.has_next_open_orders`, `.bar_event_requirements`, `._due_next_open` and `.on_bar`.

```bash
git commit -F - <<'EOF'
feat(backtest): next-open fills and whole-share clamps in the simulator

A fill_at_next_open order now fills at the OPEN of the first bar that
starts after its decision, stamped at that open and priced with the
market cost model, through the new on_bar. Ordinary close quotes skip it.
It gets one shot: what that open does not fill is dropped and counted in
next_open_expired_order_count, never left in unfilled_order_count. A
whole_shares order floors any clamp to a whole share. The new summary
keys appear only on runs that submitted a next-open order.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 4: Bracket legs: arming, the five trigger rules, costs and one-cancels-other

**Files:**
- Modify: `backend/simulated_execution.py`: `__init__`, `submit`, `bar_event_requirements`, `on_quote` (after a fill is committed), `on_bar` and `execution_summary`; new `cancel`, `has_bracket_legs`, `bracket_legs`, `_arm_bracket_legs`, `_shrink_bracket_legs`, `_close_bracket` and `_trigger_bracket_leg`
- Test: `backend/tests/test_swing_port_bracket_legs.py`

**Interfaces:**
- Consumes: from Task 2, `SimulationOrder.bracket` and `SimulationFill.exit_reason`. From Task 3, `on_bar`, `_bar_cursor` and `bar_event_requirements`.
- Produces:
  - `NextEventExecutionSimulator.cancel(order_id) -> bool`
  - `.has_bracket_legs` (property, bool)
  - `.bracket_legs` (property, a tuple of dicts `{"parent_id", "symbol", "qty", "stop_loss_price", "take_profit_price", "armed_from_bar_ts"}`)
  - fill sources `bracket_sl:<pid>`, `bracket_sl_gap:<pid>`, `bracket_tp:<pid>` and `bracket_tp_gap:<pid>`, with leg order ids `<pid>:sl` and `<pid>:tp`
  - summary keys `bracket_order_count`, `bracket_open_leg_count`, `bracket_exit_counts` and `cancelled_order_count`

- [ ] **Step 1: Impact analysis.** Run `mcp__gitnexus__impact` upstream on `NextEventExecutionSimulator.on_quote`, `.on_bar`, `.submit`, `.bar_event_requirements` and `.execution_summary`. Grep fallback:

```bash
grep -rn "\.on_quote(\|\.on_bar(\|\.execution_summary()\|_execution_simulator.submit(" backend --include='*.py' | grep -v /tests/
```

Expected today: `on_quote` has 1 production caller (`PortfolioEmulator.process_quote`), plus `on_bar` itself from Task 3, and it sits on every equity backtest's fill path. Report HIGH blast radius, guarded by Task 1. The new branches in `on_quote` run only when `order.bracket is not None`, or when a sell fills while `_bracket_legs` is non-empty.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_swing_port_bracket_legs.py`:

```python
"""Bracket legs trigger off each bar's high and low (spec 6.2)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from simulated_execution import (  # noqa: E402
    ExecutionCostModel,
    NextEventExecutionSimulator,
    SimulationBarEvent,
    SimulationOrder,
    SimulationQuote,
)

COSTS = ExecutionCostModel(version="test-v1", spread_bps=20.0,
                           slippage_bps=10.0, fee_bps=5.0,
                           latency=timedelta(0))
DECISION = datetime(2026, 3, 2, 13, 0)
DAY = timedelta(days=1)
OPEN0 = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)
CLOSE0 = datetime(2026, 3, 2, 21, 0, tzinfo=timezone.utc)
STOP, TARGET = 94.0, 109.0


def _bar(day=0, o=100.0, h=104.0, l=97.0, c=102.0):
    return SimulationBarEvent(symbol="AAPL", open=o, high=h, low=l, close=c,
                              bar_ts=OPEN0 + day * DAY,
                              available_at=CLOSE0 + day * DAY)


def _parent(**overrides):
    values = dict(order_id="p1", symbol="AAPL", side="buy", quantity=10.0,
                  decision_at=DECISION, execute_not_before=DECISION,
                  source="main_signal", fill_at_next_open=True,
                  whole_shares=True,
                  bracket={"take_profit_price": TARGET,
                           "stop_loss_price": STOP})
    values.update(overrides)
    return SimulationOrder(**values)


def _armed():
    """A 10-share parent filled at Monday's open, legs armed, Monday quiet."""
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_parent())
    [buy] = sim.on_bar(_bar(0))
    assert buy.side == "buy"
    return sim


def test_a_filled_parent_arms_legs_that_are_not_pending_orders():
    sim = _armed()
    [leg] = sim.bracket_legs
    assert leg == {"parent_id": "p1", "symbol": "AAPL", "qty": 10.0,
                   "stop_loss_price": STOP, "take_profit_price": TARGET,
                   "armed_from_bar_ts": OPEN0}
    assert sim.has_bracket_legs is True
    assert sim.pending_orders == ()
    assert sim.pending_symbols == ()
    assert sim.execution_summary()["unfilled_order_count"] == 0


def _stop_price(trigger):
    touch = trigger * (1 - 20.0 / 20_000)
    return touch, touch * (1 - 10.0 / 10_000)


@pytest.mark.parametrize("rule, bar, source, reason, price, stamped", [
    ("1 gap through the stop", dict(o=93.0, h=95.0, l=90.0, c=91.0),
     "bracket_sl_gap:p1", "stop_loss", _stop_price(93.0)[1], "open"),
    ("2 gap through the target", dict(o=111.0, h=113.0, l=108.0, c=112.0),
     "bracket_tp_gap:p1", "take_profit", 111.0, "open"),
    ("3 both touched: the stop wins", dict(o=100.0, h=110.0, l=93.0, c=100.0),
     "bracket_sl:p1", "stop_loss", _stop_price(STOP)[1], "close"),
    ("4 low reaches the stop", dict(o=100.0, h=104.0, l=94.0, c=95.0),
     "bracket_sl:p1", "stop_loss", _stop_price(STOP)[1], "close"),
    ("5 high reaches the target", dict(o=100.0, h=109.0, l=99.0, c=108.0),
     "bracket_tp:p1", "take_profit", TARGET, "close"),
])
def test_each_trigger_rule(rule, bar, source, reason, price, stamped):
    sim = _armed()
    [fill] = sim.on_bar(_bar(1, **bar))
    assert fill.side == "sell"
    assert fill.source == source
    assert fill.exit_reason == reason
    assert fill.order_id == f"p1:{'sl' if reason == 'stop_loss' else 'tp'}"
    assert fill.incremental_quantity == fill.cumulative_quantity == 10.0
    assert fill.price == pytest.approx(price)
    assert fill.fees == pytest.approx(10.0 * price * 5.0 / 10_000)
    when = (OPEN0 if stamped == "open" else CLOSE0) + DAY
    assert fill.quote_timestamp == fill.executed_at == when
    assert sim.bracket_legs == ()


def test_a_split_shaped_gap_is_traded_as_a_gap():
    """Bars are fetched adjustment=split, so a halving in the cache is a real
    move or a stale cache to purge -- the reading that keeps the emulator's
    split reconcile off. No special case: the stop fills at the open."""
    sim = _armed()
    [stop] = sim.on_bar(_bar(1, o=50.5, h=51.0, l=50.0, c=50.7))
    assert stop.source == "bracket_sl_gap:p1"
    assert stop.price == pytest.approx(_stop_price(50.5)[1])


def test_a_quiet_bar_triggers_nothing():
    sim = _armed()
    assert sim.on_bar(_bar(1, o=100.0, h=108.99, l=94.01, c=101.0)) == []
    assert len(sim.bracket_legs) == 1


def test_the_stop_pays_spread_and_slippage_the_target_pays_neither():
    sim = _armed()
    [stop] = sim.on_bar(_bar(1, o=100.0, h=101.0, l=90.0, c=91.0))
    touch, price = _stop_price(STOP)
    assert stop.spread_cost == pytest.approx((STOP - touch) * 10)
    assert stop.slippage_cost == pytest.approx((touch - price) * 10)
    sim = _armed()
    [target] = sim.on_bar(_bar(1, o=100.0, h=120.0, l=99.0, c=118.0))
    assert target.spread_cost == 0.0
    assert target.slippage_cost == 0.0
    assert target.fees > 0.0


def test_the_parents_own_fill_bar_is_checked():
    """Filled at the open, so the whole bar's range happened after the fill."""
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_parent())
    buy, stop = sim.on_bar(_bar(0, o=100.0, h=101.0, l=93.0, c=94.5))
    assert (buy.side, stop.source) == ("buy", "bracket_sl:p1")


def test_an_open_below_the_stop_buys_and_stops_out_at_the_same_open():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_parent())
    buy, stop = sim.on_bar(_bar(0, o=92.0, h=95.0, l=91.0, c=93.0))
    assert buy.quote_timestamp == stop.quote_timestamp == OPEN0
    assert stop.source == "bracket_sl_gap:p1"
    assert stop.price < buy.price


def test_a_parent_filled_at_a_close_is_checked_from_the_next_bar():
    sim = NextEventExecutionSimulator(COSTS)
    sim.submit(_parent(fill_at_next_open=False,
                       execute_not_before=DECISION + DAY))
    quote = SimulationQuote.from_mid(symbol="AAPL", timestamp=CLOSE0 + DAY,
                                     mid=100.0, spread_bps=20.0)
    [buy] = sim.on_quote(quote)
    assert sim.bracket_legs[0]["armed_from_bar_ts"] == CLOSE0 + DAY
    # Tuesday's range happened BEFORE the fill at Tuesday's close.
    assert sim.on_bar(_bar(1, o=100.0, h=101.0, l=80.0, c=100.0)) == []
    [stop] = sim.on_bar(_bar(2, o=99.0, h=100.0, l=93.0, c=95.0))
    assert stop.source == "bracket_sl:p1"


def test_one_cancels_other_and_the_pending_strategy_sell_is_cancelled():
    sim = _armed()
    sim.submit(SimulationOrder(
        order_id="s1", symbol="AAPL", side="sell", quantity=10.0,
        decision_at=CLOSE0 + DAY, execute_not_before=CLOSE0 + 2 * DAY,
        source="main_signal"))
    [stop] = sim.on_bar(_bar(1, o=100.0, h=101.0, l=90.0, c=91.0))
    assert stop.exit_reason == "stop_loss"
    assert sim.pending_orders == ()
    # The sibling target is gone: a later bar through it fills nothing.
    assert sim.on_bar(_bar(2, o=100.0, h=130.0, l=99.0, c=120.0)) == []
    summary = sim.execution_summary()
    assert summary["cancelled_order_count"] == 1
    assert summary["unfilled_order_count"] == 0
    assert summary["bracket_exit_counts"] == {
        "stop_loss": 1, "stop_loss_gap": 0,
        "take_profit": 0, "take_profit_gap": 0}
    assert summary["bracket_open_leg_count"] == 0
    assert summary["bracket_order_count"] == 1


def test_a_strategy_sell_that_fills_shrinks_then_deletes_the_legs():
    sim = _armed()
    steps = (("s1", 4.0, 6.0), ("s2", 6.0, None))
    for minute, (order_id, qty, legs_left) in enumerate(steps, start=1):
        sim.submit(SimulationOrder(
            order_id=order_id, symbol="AAPL", side="sell", quantity=qty,
            decision_at=CLOSE0, execute_not_before=CLOSE0,
            source="main_signal"))
        quote = SimulationQuote.from_mid(
            symbol="AAPL", timestamp=CLOSE0 + timedelta(minutes=minute),
            mid=100.0, spread_bps=20.0)
        [sell] = sim.on_quote(quote)
        assert sell.incremental_quantity == qty
        legs = sim.bracket_legs
        assert (legs[0]["qty"] if legs else None) == legs_left


def test_a_leg_sells_no_more_than_is_held():
    sim = _armed()
    [stop] = sim.on_bar(_bar(1, o=100.0, h=101.0, l=90.0, c=91.0),
                        position_of=lambda symbol: 3.0)
    assert stop.incremental_quantity == 3.0


def test_a_leg_with_nothing_left_to_protect_is_dropped_without_a_fill():
    sim = _armed()
    assert sim.on_bar(_bar(1, o=100.0, h=101.0, l=90.0, c=91.0),
                      position_of=lambda symbol: 0.0) == []
    assert sim.bracket_legs == ()


def test_legs_are_requirements_until_they_close():
    sim = _armed()
    assert sim.bar_event_requirements() == {"AAPL": OPEN0}
    sim.on_bar(_bar(1, o=100.0, h=101.0, l=90.0, c=91.0))
    assert sim.bar_event_requirements() == {}
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_port_bracket_legs.py -q -p no:cacheprovider`
Expected: FAIL, with `AttributeError: 'NextEventExecutionSimulator' object has no attribute 'bracket_legs'` and the trigger tests returning `[]` where a stop or target fill is expected.

- [ ] **Step 4: Add the leg state and counters**

In `backend/simulated_execution.py`, replace:

```python
        self._next_open_expired_count = 0
```

with:

```python
        self._next_open_expired_count = 0
        self._bracket_legs: dict[str, dict] = {}
        self._cancelled_order_count = 0
        self._bracket_order_count = 0
        self._bracket_exit_counts = {
            "stop_loss": 0, "stop_loss_gap": 0,
            "take_profit": 0, "take_profit_gap": 0,
        }
```

- [ ] **Step 5: Count brackets, and add `cancel`, `has_bracket_legs` and `bracket_legs`**

In `backend/simulated_execution.py`, replace:

```python
        if order.fill_at_next_open:
            self._next_open_order_count += 1
```

with:

```python
        if order.bracket is not None:
            self._bracket_order_count += 1
        if order.fill_at_next_open:
            self._next_open_order_count += 1

    def cancel(self, order_id) -> bool:
        """Withdraw a pending order. True when one was pending.

        Only the bracket one-cancels-other rule calls this today. A cancelled
        order is not pending, so it never reaches `unfilled_order_count`; it
        is counted in `cancelled_order_count` instead.
        """
        state = self._pending.pop(str(order_id or "").strip(), None)
        if state is None:
            return False
        self._cancelled_order_count += 1
        return True

    @property
    def has_bracket_legs(self) -> bool:
        return bool(self._bracket_legs)

    @property
    def bracket_legs(self) -> tuple[dict, ...]:
        """Armed legs, oldest first, as copies with `parent_id` added.

        Deliberately NOT part of `pending_orders`: a leg is a contingent exit
        on shares already held, not an order waiting to fill, so it must not
        count as unfilled or make its symbol look pending to a guard.
        """
        return tuple(
            {"parent_id": parent_id, **leg}
            for parent_id, leg in self._bracket_legs.items()
        )
```

- [ ] **Step 6: Make armed legs a bar requirement**

In `backend/simulated_execution.py`, replace:

```python
        for state in self._pending.values():
            if state.order.fill_at_next_open:
                _need(state.order.symbol, state.order.decision_at)
```

with:

```python
        for state in self._pending.values():
            if state.order.fill_at_next_open:
                _need(state.order.symbol, state.order.decision_at)
        for leg in self._bracket_legs.values():
            _need(leg["symbol"], leg["armed_from_bar_ts"])
```

- [ ] **Step 7: Arm on a parent's fill; shrink on a strategy sell's fill**

In `backend/simulated_execution.py`, replace:

```python
            liquidity -= incremental
            if is_final:
                completed.append(order_id)
```

with:

```python
            liquidity -= incremental
            if is_final:
                completed.append(order_id)
            if order.bracket is not None:
                self._arm_bracket_legs(order, incremental, quote.timestamp)
            elif order.side == "sell" and self._bracket_legs:
                self._shrink_bracket_legs(order.symbol, incremental)
```

- [ ] **Step 8: Add the arming, shrinking and one-cancels-other helpers**

In `backend/simulated_execution.py`, replace:

```python
    # -- swing port: next-open fills and bracket legs (spec 6.2) -------------
```

with:

```python
    # -- swing port: next-open fills and bracket legs (spec 6.2) -------------

    def _arm_bracket_legs(self, order, quantity, armed_from) -> None:
        """Arm (or grow) the legs for a bracket parent's fill.

        `armed_from` is the fill's quote time. A leg checks every bar whose
        bar_ts is at or after it: a next-open fill is stamped at its bar's
        open, so that bar's whole range counts; a fill at a close is stamped
        after its bar's open, so the check starts with the next bar.
        """
        leg = self._bracket_legs.get(order.order_id)
        if leg is None:
            self._bracket_legs[order.order_id] = {
                "symbol": order.symbol,
                "qty": float(quantity),
                "stop_loss_price": order.bracket["stop_loss_price"],
                "take_profit_price": order.bracket["take_profit_price"],
                "armed_from_bar_ts": armed_from,
            }
        else:
            leg["qty"] += float(quantity)

    def _shrink_bracket_legs(self, symbol, quantity) -> None:
        """A strategy sell that FILLED takes its shares out of the legs,
        oldest parent first; a leg left with nothing is deleted."""
        remaining = float(quantity)
        for parent_id in tuple(self._bracket_legs):
            if remaining <= 1e-12:
                break
            leg = self._bracket_legs[parent_id]
            if leg["symbol"] != symbol:
                continue
            taken = min(leg["qty"], remaining)
            leg["qty"] -= taken
            remaining -= taken
            if leg["qty"] <= 1e-9:
                del self._bracket_legs[parent_id]

    def _close_bracket(self, parent_id, symbol) -> None:
        """One-cancels-other: the legs go, and so does anything still waiting
        to sell the same shares -- a strategy sell, or the parent's unfilled
        remainder."""
        self._bracket_legs.pop(parent_id, None)
        self.cancel(parent_id)
        for order_id, state in tuple(self._pending.items()):
            if state.order.symbol == symbol and state.order.side == "sell":
                self.cancel(order_id)
```

- [ ] **Step 9: Check the legs in `on_bar`, and add the trigger rules**

In `backend/simulated_execution.py`, replace:

```python
                if self._pending.pop(order_id, None) is not None:
                    self._next_open_expired_count += 1

        self._bar_cursor[event.symbol] = bar_seconds
        return emitted
```

with:

```python
                if self._pending.pop(order_id, None) is not None:
                    self._next_open_expired_count += 1

        for parent_id in tuple(self._bracket_legs):
            leg = self._bracket_legs.get(parent_id)
            if leg is None or leg["symbol"] != event.symbol:
                continue
            if _event_seconds(leg["armed_from_bar_ts"],
                              field="armed_from_bar_ts") > bar_seconds:
                continue
            fill = self._trigger_bracket_leg(
                parent_id, leg, event,
                accept_fill=accept_fill, position_of=position_of)
            if fill is not None:
                emitted.append(fill)

        self._bar_cursor[event.symbol] = bar_seconds
        return emitted

    def _trigger_bracket_leg(self, parent_id, leg, event, *, accept_fill,
                             position_of):
        """Spec 6.2's five rules, first match wins:

        1. open <= stop            -> stop fills AT THE OPEN (gapped through)
        2. open >= target          -> target fills AT THE OPEN
        3. low <= stop, high >= target -> the STOP, at the stop price: the path
           inside a bar is unknown, so assume the worse outcome
        4. low <= stop             -> stop at the stop price
        5. high >= target          -> target at the target price

        Rule 3 is rule 4 reached first, which is the point of the ordering.
        """
        stop = leg["stop_loss_price"]
        target = leg["take_profit_price"]
        if event.open <= stop:
            kind, trigger, stamp = "stop_loss_gap", event.open, event.bar_ts
        elif event.open >= target:
            kind, trigger, stamp = "take_profit_gap", event.open, event.bar_ts
        elif event.low <= stop:
            kind, trigger, stamp = "stop_loss", stop, event.available_at
        elif event.high >= target:
            kind, trigger, stamp = "take_profit", target, event.available_at
        else:
            return None
        quantity = float(leg["qty"])
        if position_of is not None:
            try:
                held = max(0.0, float(position_of(event.symbol) or 0.0))
            except (TypeError, ValueError):
                held = quantity
            quantity = min(quantity, held)
        if quantity <= 1e-12:
            # Nothing left to protect: the shares went some other way.
            self._close_bracket(parent_id, event.symbol)
            return None
        model = self._model_for(event.symbol)
        is_stop = kind.startswith("stop_loss")
        if is_stop:
            # A triggered stop is a market sell: it pays the half spread and
            # the slippage, measured from the trigger.
            touch = trigger * (1.0 - model.spread_bps / 20_000.0)
            price = touch * (1.0 - model.slippage_bps / 10_000.0)
            spread_cost = abs(trigger - touch) * quantity
            slippage_cost = abs(touch - price) * quantity
        else:
            # The target is a resting limit: like a passive fill it crosses
            # nothing, and the fee is its only cost.
            price = trigger
            spread_cost = slippage_cost = 0.0
        price = _finite_number(price, field="bracket fill price", positive=True)
        fees = quantity * price * model.fee_bps / 10_000.0
        leg_code = "sl" if is_stop else "tp"
        gap = "_gap" if kind.endswith("_gap") else ""
        fill = SimulationFill(
            order_id=f"{parent_id}:{leg_code}",
            symbol=event.symbol,
            side="sell",
            incremental_quantity=quantity,
            cumulative_quantity=quantity,
            price=price,
            fees=fees,
            spread_cost=spread_cost,
            slippage_cost=slippage_cost,
            quote_timestamp=stamp,
            executed_at=stamp,
            cost_model_version=self.cost_model.version,
            source=f"bracket_{leg_code}{gap}:{parent_id}",
            order_quantity=quantity,
            is_final=True,
            exit_reason="stop_loss" if is_stop else "take_profit",
        )
        if accept_fill is not None:
            accept_fill(fill)
        self._fills.append(fill)
        self._bracket_exit_counts[kind] += 1
        self._close_bracket(parent_id, event.symbol)
        return fill
```

- [ ] **Step 10: Add the bracket summary keys, only when used**

In `backend/simulated_execution.py`, replace:

```python
            summary["next_open_expired_order_count"] = (
                self._next_open_expired_count)
        return summary
```

with:

```python
            summary["next_open_expired_order_count"] = (
                self._next_open_expired_count)
        if self._bracket_order_count:
            summary["bracket_order_count"] = self._bracket_order_count
            summary["bracket_open_leg_count"] = len(self._bracket_legs)
            summary["bracket_exit_counts"] = dict(self._bracket_exit_counts)
            summary["cancelled_order_count"] = self._cancelled_order_count
        return summary
```

- [ ] **Step 11: Run the new tests, the guard and the existing tests**

Run: `python3 -m pytest backend/tests/test_swing_port_bracket_legs.py backend/tests/test_swing_port_next_open.py backend/tests/test_swing_port_sim_types.py backend/tests/test_swing_port_invariance.py backend/tests/test_simulated_execution.py backend/tests/test_passive_limit_execution.py backend/tests/test_portfolio_emulator_fills.py -q -p no:cacheprovider`
Expected: all pass (17 new).

- [ ] **Step 12: Check scope, then commit**

```bash
git add backend/simulated_execution.py backend/tests/test_swing_port_bracket_legs.py
```

Run `mcp__gitnexus__detect_changes({scope: "staged", repo: "IntelliStock"})`. Expected changed symbols: `NextEventExecutionSimulator.__init__`, `.submit`, `.bar_event_requirements`, `.on_quote`, `.on_bar` and `.execution_summary`, plus the new `.cancel`, `.has_bracket_legs`, `.bracket_legs`, `._arm_bracket_legs`, `._shrink_bracket_legs`, `._close_bracket` and `._trigger_bracket_leg`.

```bash
git commit -F - <<'EOF'
feat(backtest): bracket legs that trigger off each bar's high and low

A filled bracket parent arms a stop leg and a target leg, kept outside
the pending orders so unfilled counts and pending-symbol guards are
unchanged. Each later bar applies the spec's five rules: gaps fill at the
open, and a bar that touches both levels fills the stop. A stop pays the
market-sell spread and slippage; a target pays only the fee. A leg fill
cancels its sibling and any pending strategy sell for the symbol, and a
strategy sell that fills shrinks the legs. Bracket summary keys appear
only when a bracket was submitted.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 5: Emulator plumbing, and sells before buys at one open

**Files:**
- Modify: `backend/simulated_execution.py`: a new `bar_event_priority` after `_due_next_open`
- Modify: `backend/portfolio_emulator.py`:
  - imports (22-49)
  - `apply_fill` (1244-1260)
  - `process_quote` (1262-1311)
  - new `has_bracket_legs`, `has_next_open_orders`, `bar_event_requirements`, `_position_quantity`, `process_bar_events`, `_book_reservation_fills` and `_drop_stale_reservations`
  - `execute_signal` (1473-1625)
- Test: `backend/tests/test_swing_port_emulator_bars.py`; append to `backend/tests/test_swing_port_invariance.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces:
  - `PortfolioEmulator.execute_signal(..., bracket=None, whole_shares=False, fill_at_next_open=False)`
  - `.has_bracket_legs() -> bool`, `.has_next_open_orders() -> bool` and `.bar_event_requirements() -> dict[str, datetime]`
  - `.process_bar_events(bars_by_symbol: dict[str, list[dict]], clock) -> list[SimulationFill]`
  - `NextEventExecutionSimulator.bar_event_priority(event) -> int`
  - trade rows gain `"exit_reason"` only on bracket fills

- [ ] **Step 1: Impact analysis.** Run `mcp__gitnexus__impact` upstream on `PortfolioEmulator.execute_signal`, `.process_quote` and `.apply_fill`. Grep fallback:

```bash
grep -rn "execute_signal\b\|\.process_quote(\|\.apply_fill\b" backend --include='*.py' | grep -v /tests/ | grep -v "def "
```

Expected callers:
- `execute_signal`: broker.py's 7 `_submit_portfolio_signal` sites, `broker.py:18991` (the non-equity executor submit), `broker.py:8812` (`_gated_execute_signal`), and the live adapters' same-named methods (unaffected).
- `process_quote`: `process_price_event` and `process_price_events`.

`process_quote` is refactored into two helpers with identical behaviour. Task 1's golden and `test_portfolio_emulator_fills.py` guard it. Report HIGH blast radius.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_swing_port_emulator_bars.py`:

```python
"""PortfolioEmulator plumbing for next-open fills and bracket legs (spec 6.2)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from portfolio_emulator import PortfolioEmulator  # noqa: E402
from simulated_execution import (  # noqa: E402
    ExecutionCostModel,
    NextEventExecutionSimulator,
)

COSTS = ExecutionCostModel(version="test-v1", spread_bps=20.0,
                           slippage_bps=10.0, fee_bps=5.0,
                           latency=timedelta(0))
DAY = timedelta(days=1)
DECISION = datetime(2026, 3, 2, 13, 0)          # Monday 05:00 PT, naive UTC
OPEN0 = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)
CLOSE0 = datetime(2026, 3, 2, 21, 0, tzinfo=timezone.utc)
BRACKET = {"take_profit_price": 109.0, "stop_loss_price": 94.0}


def _emulator(cash=10_000.0):
    sim = NextEventExecutionSimulator(COSTS)
    return PortfolioEmulator(cash, execution_simulator=sim,
                             execution_delay=DAY), sim


def _bar(day=0, o=100.0, h=104.0, l=97.0, c=102.0):
    return {"t": f"2026-03-0{2 + day}T05:00:00Z", "o": o, "h": h, "l": l,
            "c": c, "bar_ts": OPEN0 + day * DAY,
            "available_at": CLOSE0 + day * DAY}


def _clock(day):
    """The tick that first sees bar `day`: 05:00 PT the next day."""
    return DECISION + (day + 1) * DAY


def _swing_buy(emu, symbol="AAPL", cash=1250.0, price=100.0, when=DECISION):
    return emu.execute_signal(
        symbol, 1, price, timestamp=when, cash_per_trade=cash,
        order_source="main_signal", bracket=dict(BRACKET), whole_shares=True,
        fill_at_next_open=True)


def test_no_simulator_means_no_bar_work():
    emu = PortfolioEmulator(1_000.0)
    assert emu.has_bracket_legs() is False
    assert emu.has_next_open_orders() is False
    assert emu.bar_event_requirements() == {}
    assert emu.process_bar_events({"AAPL": [_bar()]}, _clock(0)) == []


def test_a_whole_share_next_open_order_is_a_quantity_order_with_no_delay():
    emu, sim = _emulator()
    assert _swing_buy(emu)
    [order] = sim.pending_orders
    assert order.quantity == 12.0            # 1250 / all-in 100.25 = 12.47
    assert order.whole_shares is True
    assert order.notional_limit is None
    assert order.execute_not_before == order.decision_at == DECISION
    assert order.bracket == BRACKET
    assert emu.has_next_open_orders() is True


def test_a_whole_share_buy_too_small_for_one_share_is_not_submitted():
    emu, sim = _emulator()
    assert _swing_buy(emu, cash=90.0) is False
    assert sim.pending_orders == ()


def test_a_next_open_order_never_rests_passively():
    emu, sim = _emulator()
    PortfolioEmulator.set_passive_execution(True, 8)
    try:
        assert _swing_buy(emu)
        emu.execute_signal("MSFT", 1, 50.0, timestamp=DECISION,
                           cash_per_trade=500.0, order_source="main_signal")
    finally:
        PortfolioEmulator._PASSIVE_OVERRIDE = None
    next_open, ordinary = sim.pending_orders
    assert next_open.limit_price is None
    assert ordinary.limit_price == 50.0


def test_entry_fill_then_stop_books_trades_with_source_and_exit_reason():
    emu, sim = _emulator()
    _swing_buy(emu)
    fills = emu.process_bar_events({"AAPL": [_bar(0)]}, _clock(0))
    assert [f.side for f in fills] == ["buy"]
    assert emu.get_positions() == {"AAPL": 12.0}
    assert emu.has_bracket_legs() is True
    assert emu.pending_execution_symbols() == ()
    assert emu._execution_cash_reservations == {}

    fills = emu.process_bar_events(
        {"AAPL": [_bar(1, o=100.0, h=101.0, l=90.0, c=91.0)]}, _clock(1))
    assert [f.source for f in fills] == [f"bracket_sl:{sim.fills[0].order_id}"]
    assert emu.get_positions() == {}
    buy_row, stop_row = emu.get_trade_history()
    assert "exit_reason" not in buy_row
    assert stop_row["exit_reason"] == "stop_loss"
    assert stop_row["source"].startswith("bracket_sl:")
    assert stop_row["timestamp"] == CLOSE0 + DAY
    summary = emu.get_execution_summary()
    assert summary["unfilled_order_count"] == 0
    assert summary["bracket_exit_counts"]["stop_loss"] == 1


def test_a_stop_cancels_the_pending_strategy_sell_and_its_reservation():
    emu, sim = _emulator()
    _swing_buy(emu)
    emu.process_bar_events({"AAPL": [_bar(0)]}, _clock(0))
    # An ordinary (close-filled) strategy sell, waiting for Wednesday's close.
    assert emu.execute_signal("AAPL", -1, 102.0, timestamp=_clock(0),
                              sell_fraction=1.0, order_source="main_signal")
    assert emu.pending_execution_symbols() == ("AAPL",)
    emu.process_bar_events(
        {"AAPL": [_bar(1, o=100.0, h=101.0, l=90.0, c=91.0)]}, _clock(1))
    assert emu.pending_execution_symbols() == ()
    assert emu._execution_position_reservations == {}
    assert emu.get_execution_summary()["cancelled_order_count"] == 1


def test_at_one_open_the_exit_funds_the_entry():
    """MSFT's RSI exit and AAPL's entry both fill at Tuesday's open. Sorted
    by name, AAPL would go first and find no cash. (The entry is SIZED against
    the pending sale only with backtest_credit_pending_sell_proceeds on.)"""
    emu, sim = _emulator(cash=1_300.0)
    emu.credit_pending_sell_proceeds = True
    emu.execute_signal("MSFT", 1, 100.0, timestamp=DECISION,
                       cash_per_trade=1_250.0, order_source="main_signal",
                       whole_shares=True, fill_at_next_open=True)
    emu.process_bar_events({"MSFT": [_bar(0)]}, _clock(0))
    assert emu.get_positions() == {"MSFT": 12.0}
    emu.execute_signal("MSFT", -1, 102.0, timestamp=_clock(0),
                       sell_fraction=1.0, order_source="main_signal",
                       fill_at_next_open=True)
    _swing_buy(emu, cash=1_100.0, price=102.0, when=_clock(0))
    fills = emu.process_bar_events(
        {"AAPL": [_bar(1, o=101.0)], "MSFT": [_bar(1, o=103.0)]}, _clock(1))
    assert [(f.symbol, f.side) for f in fills] == [
        ("MSFT", "sell"), ("AAPL", "buy")]
    assert emu.get_positions()["AAPL"] == 10.0


def test_an_rsi_exit_at_the_open_beats_a_stop_later_in_the_same_bar():
    """Live, the engine cancels the legs before the exit's market order
    reaches the open auction; the backtest must agree."""
    emu, _sim = _emulator()
    _swing_buy(emu)
    emu.process_bar_events({"AAPL": [_bar(0)]}, _clock(0))
    emu.execute_signal("AAPL", -1, 102.0, timestamp=_clock(0),
                       sell_fraction=1.0, order_source="main_signal",
                       fill_at_next_open=True)
    [sell] = emu.process_bar_events(
        {"AAPL": [_bar(1, o=100.0, h=101.0, l=90.0, c=92.0)]}, _clock(1))
    assert (sell.source, sell.quote_timestamp) == ("main_signal", OPEN0 + DAY)
    summary = emu.get_execution_summary()
    assert summary["bracket_exit_counts"] == {
        "stop_loss": 0, "stop_loss_gap": 0,
        "take_profit": 0, "take_profit_gap": 0}
    assert summary["bracket_open_leg_count"] == 0
    assert emu.get_positions() == {}


def test_an_entry_the_cash_cannot_fully_pay_for_protects_what_it_bought():
    """Sized at 9 shares on a $100 close; the open gaps up 15%, so the cash
    buys 8. The legs cover 8, the ninth share is dropped and counted, and the
    stop sells exactly 8."""
    emu, sim = _emulator(cash=1_000.0)
    emu.execute_signal("AAPL", 1, 100.0, timestamp=DECISION,
                       cash_per_trade=1_000.0, order_source="main_signal",
                       whole_shares=True, fill_at_next_open=True,
                       bracket={"take_profit_price": 125.0,
                                "stop_loss_price": 108.0})
    [buy] = emu.process_bar_events(
        {"AAPL": [_bar(0, o=115.0, h=116.0, l=114.0, c=115.0)]}, _clock(0))
    assert (buy.incremental_quantity, buy.order_quantity) == (8.0, 9.0)
    assert [leg["qty"] for leg in sim.bracket_legs] == [8.0]
    [stop] = emu.process_bar_events(
        {"AAPL": [_bar(1, o=115.0, h=115.0, l=90.0, c=91.0)]}, _clock(1))
    assert stop.incremental_quantity == 8.0
    summary = emu.get_execution_summary()
    assert summary["next_open_expired_order_count"] == 1
    assert summary["unfilled_order_count"] == 0
    assert emu.get_positions() == {}


def test_a_bar_from_the_future_is_a_look_ahead_bug():
    emu, _sim = _emulator()
    _swing_buy(emu)
    with pytest.raises(ValueError, match="not available"):
        emu.process_bar_events({"AAPL": [_bar(0)]}, CLOSE0 - timedelta(hours=1))


def test_requirements_pass_through():
    emu, _sim = _emulator()
    _swing_buy(emu)
    assert emu.bar_event_requirements() == {
        "AAPL": DECISION.replace(tzinfo=timezone.utc)}
```

Then append the two EB-path invariance tests.

In `backend/tests/test_swing_port_invariance.py`, replace:

```python
        "the golden changed. It may only be written once, by the pre-change "
        "code; restore it from git instead of regenerating it.")
```

with:

```python
        "the golden changed. It may only be written once, by the pre-change "
        "code; restore it from git instead of regenerating it.")


def _submit_with_explicit_defaults(emulator, day, now):
    """The same orders, each passing the three swing-port keywords at their
    defaults -- what an EB tick looks like after the change."""
    plain = emulator.execute_signal

    def execute_signal(*args, **kwargs):
        kwargs.update(bracket=None, whole_shares=False,
                      fill_at_next_open=False)
        return plain(*args, **kwargs)

    emulator.execute_signal = execute_signal
    try:
        _submit_plain(emulator, day, now)
    finally:
        del emulator.execute_signal


def test_default_swing_keywords_change_nothing():
    assert _canonical(_run(_submit_with_explicit_defaults)) == GOLDEN.read_text()


def test_a_run_without_bar_work_never_needs_the_bar_hook():
    seen = []

    def submit(emulator, day, now):
        _submit_plain(emulator, day, now)
        seen.append((emulator.has_bracket_legs(),
                     emulator.has_next_open_orders(),
                     emulator.bar_event_requirements()))

    _run(submit)
    assert set(map(repr, seen)) == {repr((False, False, {}))}
```

(The new tests land between `test_the_golden_itself_was_not_regenerated` and the `GOLDEN_SHA256` constant; the constant is untouched.)

- [ ] **Step 3: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_port_emulator_bars.py backend/tests/test_swing_port_invariance.py -q -p no:cacheprovider`
Expected: FAIL (13 failed, 2 passed), with `TypeError: PortfolioEmulator.execute_signal() got an unexpected keyword argument 'bracket'` and `AttributeError: 'PortfolioEmulator' object has no attribute 'has_bracket_legs'`. The two original invariance tests still pass.

- [ ] **Step 4: Add `bar_event_priority` to the simulator**

In `backend/simulated_execution.py`, replace:

```python
            and bar_seconds >= _event_seconds(
                order.execute_not_before, field="execute_not_before")
        )
```

with:

```python
            and bar_seconds >= _event_seconds(
                order.execute_not_before, field="execute_not_before")
        )

    def bar_event_priority(self, event) -> int:
        """Order bars that share a bar_ts: 0 sells at the open (a next-open
        sell, or a leg the open gaps through), 1 buys at the open, 2 the rest.

        At one session open, sale proceeds fund the buys; without this an
        entry sorted alphabetically ahead of the exit it replaces is starved.
        """
        seconds = _event_seconds(event.bar_ts, field="bar_ts")
        buys = False
        for state in self._pending.values():
            order = state.order
            if order.symbol != event.symbol or not self._due_next_open(
                    order, seconds):
                continue
            if order.side == "sell":
                return 0
            buys = True
        for leg in self._bracket_legs.values():
            if (leg["symbol"] == event.symbol
                    and _event_seconds(leg["armed_from_bar_ts"],
                                       field="armed_from_bar_ts") <= seconds
                    and (event.open <= leg["stop_loss_price"]
                         or event.open >= leg["take_profit_price"])):
                return 0
        return 1 if buys else 2
```

- [ ] **Step 5: Import `SimulationBarEvent` in the emulator (both import branches)**

In `backend/portfolio_emulator.py`, replace every occurrence of:

```python
        NextEventExecutionSimulator,
        SimulationFill,
```

with:

```python
        NextEventExecutionSimulator,
        SimulationBarEvent,
        SimulationFill,
```

(There are exactly two occurrences: the bare `simulated_execution` import and the `backend.simulated_execution` fallback.)

- [ ] **Step 6: Put `exit_reason` on bracket trade rows only**

In `backend/portfolio_emulator.py`, replace:

```python
        self._confirmed_simulation_fills.append(fill)
        self._trades.append({
            "timestamp": fill.executed_at,
```

with:

```python
        self._confirmed_simulation_fills.append(fill)
        trade = {
            "timestamp": fill.executed_at,
```

In `backend/portfolio_emulator.py`, replace:

```python
            "source": fill.source,
            "cash_after": self._cash,
        })

    def process_quote(self, quote):
```

with:

```python
            "source": fill.source,
            "cash_after": self._cash,
        }
        if fill.exit_reason:
            trade["exit_reason"] = fill.exit_reason
        self._trades.append(trade)

    def process_quote(self, quote):
```

- [ ] **Step 7: Split `process_quote`'s reservation bookkeeping into two helpers (no behaviour change)**

In `backend/portfolio_emulator.py`, replace:

```python
        fills = self._execution_simulator.on_quote(
            quote,
            accept_fill=self.apply_fill,
            cash_budget=self.spendable_cash,
        )
        for fill in fills:
```

with:

```python
        fills = self._execution_simulator.on_quote(
            quote,
            accept_fill=self.apply_fill,
            cash_budget=self.spendable_cash,
        )
        self._book_reservation_fills(fills)
        self._drop_stale_reservations()
        return fills

    def _book_reservation_fills(self, fills):
        """Release what each fill consumed from its order's reservation."""
        for fill in fills:
```

In `backend/portfolio_emulator.py`, replace:

```python
                self._execution_position_reservations[
                    fill.order_id
                ] = remaining
        pending_ids = {
```

with:

```python
                self._execution_position_reservations[
                    fill.order_id
                ] = remaining

    def _drop_stale_reservations(self):
        """An order that is no longer pending reserves nothing."""
        pending_ids = {
```

In `backend/portfolio_emulator.py`, replace:

```python
            for order_id in tuple(reservations):
                if order_id not in pending_ids:
                    reservations.pop(order_id, None)
        return fills

    def pending_execution_symbols(self):
```

with:

```python
            for order_id in tuple(reservations):
                if order_id not in pending_ids:
                    reservations.pop(order_id, None)

    def pending_execution_symbols(self):
```

- [ ] **Step 8: Add the emulator's bar API**

In `backend/portfolio_emulator.py`, replace:

```python
    def pending_execution_symbols(self):
        if self._execution_simulator is None:
            return ()
        return self._execution_simulator.pending_symbols
```

with:

```python
    def pending_execution_symbols(self):
        if self._execution_simulator is None:
            return ()
        return self._execution_simulator.pending_symbols

    def has_bracket_legs(self):
        """True while any bracket leg is armed (spec 6.2)."""
        sim = self._execution_simulator
        return sim is not None and sim.has_bracket_legs

    def has_next_open_orders(self):
        """True while any `fill_at_next_open` order is waiting for its open."""
        sim = self._execution_simulator
        return sim is not None and sim.has_next_open_orders

    def bar_event_requirements(self):
        """{symbol: earliest bar_ts still needed} for the broker's bar
        collector; empty when there is no bar-driven work."""
        sim = self._execution_simulator
        if sim is None:
            return {}
        return sim.bar_event_requirements()

    def _position_quantity(self, symbol):
        return float(self._positions.get(symbol, 0.0) or 0.0)

    def process_bar_events(self, bars_by_symbol, clock):
        """Apply next-open fills and bracket legs for every bar handed in.

        ``bars_by_symbol`` is `backtest_bar_events.collect_bar_events`'s output:
        {symbol: [{"t", "o", "h", "l", "c", "bar_ts", "available_at"}, ...]}.
        A bar not yet available at ``clock`` is a look-ahead bug in the caller
        and raises. Bars run oldest first; bars sharing a bar_ts run sells at
        the open, then buys at the open, then the rest
        (`NextEventExecutionSimulator.bar_event_priority`). Returns the fills.
        """
        sim = self._execution_simulator
        if sim is None:
            return []
        now = _as_utc(clock)
        if now is None:
            raise ValueError("clock must be a datetime")
        grouped = {}
        for symbol, bars in (bars_by_symbol or {}).items():
            for bar in bars or ():
                event = SimulationBarEvent(
                    symbol=symbol,
                    open=bar["o"],
                    high=bar["h"],
                    low=bar["l"],
                    close=bar["c"],
                    bar_ts=bar["bar_ts"],
                    available_at=bar["available_at"],
                )
                if _as_utc(event.available_at) > now:
                    raise ValueError(
                        f"bar {event.symbol} {event.bar_ts} is not available "
                        f"at {clock}")
                grouped.setdefault(_as_utc(event.bar_ts), []).append(event)
        emitted = []
        for bar_ts in sorted(grouped):
            batch = sorted(
                grouped[bar_ts],
                key=lambda ev: (sim.bar_event_priority(ev), ev.symbol))
            for event in batch:
                fills = sim.on_bar(
                    event,
                    accept_fill=self.apply_fill,
                    cash_budget=self.spendable_cash,
                    position_of=self._position_quantity,
                )
                self._book_reservation_fills(fills)
                emitted.extend(fills)
        self._drop_stale_reservations()
        return emitted
```

- [ ] **Step 9: Accept the three keywords in `execute_signal`**

In `backend/portfolio_emulator.py`, replace:

```python
        sell_fraction=1.0,
        order_source=None,
    ):
        """
        Convenience: execute a strategy signal (1=buy, -1=sell, 0=hold) with a simple rule.
```

with:

```python
        sell_fraction=1.0,
        order_source=None,
        bracket=None,
        whole_shares=False,
        fill_at_next_open=False,
    ):
        """
        Convenience: execute a strategy signal (1=buy, -1=sell, 0=hold) with a simple rule.
```

In `backend/portfolio_emulator.py`, replace:

```python
        Returns True if a trade was executed.
        """
        if price is None:
            return False
```

with:

```python
        Returns True if a trade was executed.

        ``bracket``, ``whole_shares`` and ``fill_at_next_open`` (spec 6.2) are
        next-event-only and default to today's behaviour: the broker forwards
        them only when a strategy's sizing hint sets them. A whole-share buy is
        a QUANTITY order, as live: its share count is floored here and it
        carries no notional limit, so an opening gap moves the cost, not the
        count. A next-open order may fill at the first open after the
        decision, so it gets no execution delay and never rests passively.
        """
        if price is None:
            return False
```

In `backend/portfolio_emulator.py`, replace:

```python
                shares = self._execution_simulator.affordable_buy_quantity(
                    amount_to_use, price, symbol=ticker
                )
                side = "buy"
```

with:

```python
                shares = self._execution_simulator.affordable_buy_quantity(
                    amount_to_use, price, symbol=ticker
                )
                if whole_shares:
                    shares = float(math.floor(shares + 1e-9))
                    if shares <= 0:
                        return False
                side = "buy"
```

In `backend/portfolio_emulator.py`, replace:

```python
            _limit_px, _expire = self._passive_limit_for(side, price)
            order = SimulationOrder(
                order_id=order_id,
                symbol=ticker,
                side=side,
                quantity=shares,
                decision_at=timestamp,
                execute_not_before=timestamp + self._execution_delay,
                source=order_source.strip(),
                notional_limit=amount_to_use if side == "buy" else None,
                limit_price=_limit_px,
                expire_after_quotes=_expire,
            )
```

with:

```python
            if fill_at_next_open:
                _limit_px, _expire = None, 0
            else:
                _limit_px, _expire = self._passive_limit_for(side, price)
            _whole = bool(whole_shares) and side == "buy"
            order = SimulationOrder(
                order_id=order_id,
                symbol=ticker,
                side=side,
                quantity=shares,
                decision_at=timestamp,
                execute_not_before=(
                    timestamp if fill_at_next_open
                    else timestamp + self._execution_delay),
                source=order_source.strip(),
                notional_limit=(
                    amount_to_use if side == "buy" and not _whole else None),
                limit_price=_limit_px,
                expire_after_quotes=_expire,
                bracket=bracket if side == "buy" else None,
                whole_shares=_whole,
                fill_at_next_open=bool(fill_at_next_open),
            )
```

- [ ] **Step 10: Run the new tests, the guard and every emulator test**

Run: `python3 -m pytest backend/tests/test_swing_port_emulator_bars.py backend/tests/test_swing_port_invariance.py backend/tests/test_swing_port_bracket_legs.py backend/tests/test_swing_port_next_open.py backend/tests/test_swing_port_sim_types.py backend/tests/test_portfolio_emulator_fills.py backend/tests/test_pending_sell_proceeds.py backend/tests/test_fill_never_exceeds_spendable_cash.py backend/tests/test_backtest_live_realism.py backend/tests/test_tiered_cost_model.py backend/tests/test_strategy_eb_pending_guard.py -q -p no:cacheprovider`
Expected: all pass (11 new emulator tests; the invariance file now has 4).

- [ ] **Step 11: Check scope, then commit**

```bash
git add backend/simulated_execution.py backend/portfolio_emulator.py backend/tests/test_swing_port_emulator_bars.py backend/tests/test_swing_port_invariance.py
```

Run `mcp__gitnexus__detect_changes({scope: "staged", repo: "IntelliStock"})`. Expected changed symbols:
- `NextEventExecutionSimulator.bar_event_priority` (new);
- `PortfolioEmulator.apply_fill`, `.process_quote` and `.execute_signal`;
- the new `._book_reservation_fills`, `._drop_stale_reservations`, `.has_bracket_legs`, `.has_next_open_orders`, `.bar_event_requirements`, `._position_quantity` and `.process_bar_events`.

```bash
git commit -F - <<'EOF'
feat(backtest): emulator plumbing for next-open fills and bracket legs

execute_signal accepts bracket, whole_shares and fill_at_next_open, all
defaulting to today's behaviour. A whole-share buy is a quantity order
with its count floored. A next-open order gets no execution delay and
never rests passively. process_bar_events turns collected bars into
simulator bar events, oldest first. Bars that share an open run sells
before buys, so an exit funds the entry that replaces it. Bracket trade
rows carry exit_reason. process_quote's reservation bookkeeping moves
into two helpers that the bar path shares, with identical behaviour.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 6: `backtest_bar_events`: hint keywords, bar open times, and every bar a symbol still needs

**Files:**
- Create: `backend/backtest_bar_events.py`
- Test: `backend/tests/test_backtest_bar_events.py` and `backend/tests/test_swing_port_backtest_scenarios.py`

**Interfaces:**
- Consumes: `event_time.aware_utc`, `live_calendar._CAL` / `_HAS_LIB`, and Task 5's `PortfolioEmulator.bar_event_requirements()` / `process_bar_events()`.
- Produces:
  - `execution_hint_kwargs(nexus_hint, decision) -> dict`
  - `equity_daily_session_open(bar_start: datetime) -> datetime | None`
  - `make_bar_open_resolver(*, interval: timedelta, session_open_resolver=None) -> Callable[[dict], datetime]`
  - `collect_bar_events(data, requirements, clock, *, bar_time_to_datetime, bar_available_at, bar_open_at) -> dict[str, list[dict]]`
  - `reset_label_cache() -> None`

- [ ] **Step 1: Impact analysis.** This is a new module, so there are no upstream callers. Run `mcp__gitnexus__impact` upstream on `get_price_history_up_to_current` (file `backend/backtest_price_history.py`) only to confirm it is not modified: this module mirrors its import-safe pattern but does not touch it. Report "new module, no existing symbols modified".

- [ ] **Step 2: Write the failing unit tests**

Create `backend/tests/test_backtest_bar_events.py`:

```python
"""backtest_bar_events: hint plumbing and the bar collector (spec 6.2)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import backtest_bar_events as bbe  # noqa: E402
from bar_time import bar_time_to_datetime  # noqa: E402
from event_time import make_bar_availability_resolver  # noqa: E402

UTC = timezone.utc


# -- execution_hint_kwargs ---------------------------------------------------

@pytest.mark.parametrize("hint, decision", [
    ({"buy_cash": 1234.5}, 1),                    # EB / outlier / index core
    ({"sell_fraction": 0.4}, -1),
    ({}, 1),
    (None, 1),
    ("not-a-dict", 1),
    ({"buy_cash": 10.0, "fill_at_next_open": False, "whole_shares": False,
      "bracket": None}, 1),
    ({"fill_at_next_open": "true"}, 1),           # only the literal True
    ({"fill_at_next_open": True}, 0),             # a hold places nothing
])
def test_hints_that_set_nothing_add_nothing(hint, decision):
    assert bbe.execution_hint_kwargs(hint, decision) == {}


def test_a_swing_entry_carries_all_three():
    hint = {"buy_cash": 1250.0, "whole_shares": True, "fill_at_next_open": True,
            "bracket": {"take_profit_price": 218.0, "stop_loss_price": 188.0}}
    assert bbe.execution_hint_kwargs(hint, 1) == {
        "fill_at_next_open": True, "whole_shares": True,
        "bracket": {"take_profit_price": 218.0, "stop_loss_price": 188.0}}


def test_a_swing_exit_carries_only_the_next_open_flag():
    hint = {"sell_fraction": 1.0, "fill_at_next_open": True,
            "whole_shares": True,
            "bracket": {"take_profit_price": 2.0, "stop_loss_price": 1.0}}
    assert bbe.execution_hint_kwargs(hint, -1) == {"fill_at_next_open": True}


def test_a_malformed_bracket_is_loud():
    with pytest.raises(ValueError, match="mapping"):
        bbe.execution_hint_kwargs({"bracket": [218.0, 188.0]}, 1)


# -- bar open times ----------------------------------------------------------

def test_a_daily_bar_opens_at_the_nyse_open_across_dst():
    resolve = bbe.make_bar_open_resolver(
        interval=timedelta(days=1),
        session_open_resolver=bbe.equity_daily_session_open)
    assert resolve({"t": "2026-03-06T05:00:00Z"}) == datetime(
        2026, 3, 6, 14, 30, tzinfo=UTC)
    assert resolve({"t": "2026-03-09T04:00:00Z"}) == datetime(
        2026, 3, 9, 13, 30, tzinfo=UTC)
    with pytest.raises(ValueError, match="session open"):
        resolve({"t": "2026-02-16T05:00:00Z"})     # Presidents' Day


def test_an_intraday_bar_opens_at_its_label():
    resolve = bbe.make_bar_open_resolver(interval=timedelta(minutes=15))
    assert resolve({"t": "2026-03-02T15:45:00Z"}) == datetime(
        2026, 3, 2, 15, 45, tzinfo=UTC)


# -- collect_bar_events ------------------------------------------------------

def _session_close(bar_start):
    import live_calendar
    import pandas as pd
    session = pd.Timestamp(bar_start.date())
    if not live_calendar._CAL.is_session(session):
        return None
    return live_calendar._CAL.session_close(session).tz_convert(
        "UTC").to_pydatetime()


AVAILABLE = make_bar_availability_resolver(
    interval=timedelta(days=1), session_close_resolver=_session_close)
OPENS = bbe.make_bar_open_resolver(
    interval=timedelta(days=1),
    session_open_resolver=bbe.equity_daily_session_open)


def _daily(date, o=100.0, h=101.0, l=99.0, c=100.5):
    return {"t": f"{date}T05:00:00Z", "o": o, "h": h, "l": l, "c": c, "v": 1}


def _collect(data, requirements, clock):
    bbe.reset_label_cache()
    return bbe.collect_bar_events(
        data, requirements, clock, bar_time_to_datetime=bar_time_to_datetime,
        bar_available_at=AVAILABLE, bar_open_at=OPENS)


def test_every_bar_since_the_requirement_arrives_oldest_first():
    """Friday, then the holiday Monday has no bar, then Tuesday: a tick on
    Wednesday sees both, not just the latest."""
    data = {"AAPL": [_daily("2026-02-12"), _daily("2026-02-13", o=101.0),
                     _daily("2026-02-17", o=102.0),
                     _daily("2026-02-18", o=103.0)]}
    since = datetime(2026, 2, 13, 13, 0, tzinfo=UTC)
    clock = datetime(2026, 2, 18, 13, 0, tzinfo=UTC)
    rows = _collect(data, {"AAPL": since}, clock)["AAPL"]
    assert [r["o"] for r in rows] == [101.0, 102.0]
    assert rows[0]["bar_ts"] == datetime(2026, 2, 13, 14, 30, tzinfo=UTC)
    assert rows[0]["available_at"] == datetime(2026, 2, 13, 21, 0, tzinfo=UTC)
    assert rows[1]["t"] == "2026-02-17T05:00:00Z"


def test_a_bar_that_opened_before_the_requirement_is_left_out():
    data = {"AAPL": [_daily("2026-03-02"), _daily("2026-03-03", o=104.0)]}
    since = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)      # after Monday's open
    clock = datetime(2026, 3, 4, 13, 0, tzinfo=UTC)
    assert [r["o"] for r in _collect(data, {"AAPL": since}, clock)["AAPL"]] == [
        104.0]


def test_a_symbol_with_no_new_bar_is_absent():
    data = {"AAPL": [_daily("2026-03-02")], "HALT": []}
    clock = datetime(2026, 3, 3, 13, 0, tzinfo=UTC)
    since = datetime(2026, 3, 3, 13, 0, tzinfo=UTC)
    assert _collect(data, {"AAPL": since, "HALT": since, "GONE": since},
                    clock) == {}


def test_a_malformed_bar_is_skipped_not_fatal():
    data = {"AAPL": [_daily("2026-03-02"),
                     {"t": "2026-03-03T05:00:00Z", "o": "x", "h": 1, "l": 1,
                      "c": 1},
                     _daily("2026-03-04", o=105.0)]}
    since = datetime(2026, 3, 2, 13, 0, tzinfo=UTC)
    clock = datetime(2026, 3, 5, 13, 0, tzinfo=UTC)
    assert [r["o"] for r in _collect(data, {"AAPL": since}, clock)["AAPL"]] == [
        100.0, 105.0]


def test_labels_are_reparsed_when_the_bar_list_grows():
    bars = [_daily("2026-03-02")]
    data = {"AAPL": bars}
    since = datetime(2026, 3, 2, 13, 0, tzinfo=UTC)
    bbe.reset_label_cache()
    kwargs = dict(bar_time_to_datetime=bar_time_to_datetime,
                  bar_available_at=AVAILABLE, bar_open_at=OPENS)
    clock = datetime(2026, 3, 4, 13, 0, tzinfo=UTC)
    assert len(bbe.collect_bar_events(data, {"AAPL": since}, clock,
                                      **kwargs)["AAPL"]) == 1
    bars.append(_daily("2026-03-03", o=104.0))
    assert len(bbe.collect_bar_events(data, {"AAPL": since}, clock,
                                      **kwargs)["AAPL"]) == 2
```

- [ ] **Step 3: Write the failing scenario tests (Review Focus 3 and 5)**

Create `backend/tests/test_swing_port_backtest_scenarios.py`:

```python
"""Swing-port backtest scenarios on real NYSE sessions (spec 6.2).

Each test drives the emulator tick by tick in the broker's own order -- the
pending-fill block on the latest close, then the bar hook, then the
"strategy" (scripted here) -- over daily bars labelled the way Alpaca labels
them. Two inputs the spec is silent on: a halt, and several bars arriving
in one tick.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import backtest_bar_events as bbe  # noqa: E402
from bar_time import bar_time_to_datetime  # noqa: E402
from event_time import make_bar_availability_resolver  # noqa: E402
from portfolio_emulator import PortfolioEmulator  # noqa: E402
from simulated_execution import (  # noqa: E402
    ExecutionCostModel,
    NextEventExecutionSimulator,
    SimulationPriceEvent,
)

UTC = timezone.utc
COSTS = ExecutionCostModel(version="test-v1", spread_bps=20.0,
                           slippage_bps=10.0, fee_bps=5.0,
                           latency=timedelta(0))
BRACKET = {"take_profit_price": 109.0, "stop_loss_price": 94.0}


def _session_close(bar_start):
    import live_calendar
    import pandas as pd
    session = pd.Timestamp(bar_start.date())
    if not live_calendar._CAL.is_session(session):
        return None
    return live_calendar._CAL.session_close(session).tz_convert(
        "UTC").to_pydatetime()


AVAILABLE = make_bar_availability_resolver(
    interval=timedelta(days=1), session_close_resolver=_session_close)
OPENS = bbe.make_bar_open_resolver(
    interval=timedelta(days=1),
    session_open_resolver=bbe.equity_daily_session_open)


def _bar(date, o, h, l, c):
    return {"t": f"{date}T05:00:00Z", "o": o, "h": h, "l": l, "c": c, "v": 1}


def _at(date):
    """05:00 PT that day on the broker's naive-UTC clock (PST: 13:00 UTC)."""
    return datetime.fromisoformat(f"{date}T13:00:00")


def _emulator():
    bbe.reset_label_cache()
    return PortfolioEmulator(
        10_000.0, execution_simulator=NextEventExecutionSimulator(COSTS),
        execution_delay=timedelta(days=1))


def _tick(emu, data, now):
    """The broker's pre-strategy order: pending closes, then the bar hook."""
    clock = now.replace(tzinfo=UTC)
    events = {}
    for symbol in emu.pending_execution_symbols():
        visible = [b for b in data.get(symbol, ()) if AVAILABLE(b) <= clock]
        if visible:
            events[symbol] = SimulationPriceEvent(
                symbol=symbol, price=visible[-1]["c"],
                available_at=AVAILABLE(visible[-1]),
                bar_timestamp=datetime.fromisoformat(
                    visible[-1]["t"].replace("Z", "+00:00")))
    fills = list(emu.process_price_events(events))
    if emu.has_bracket_legs() or emu.has_next_open_orders():
        bars = bbe.collect_bar_events(
            data, emu.bar_event_requirements(), clock,
            bar_time_to_datetime=bar_time_to_datetime,
            bar_available_at=AVAILABLE, bar_open_at=OPENS)
        if bars:
            fills.extend(emu.process_bar_events(bars, clock))
    return fills


def _enter(emu, now):
    return emu.execute_signal(
        "AAPL", 1, 100.0, timestamp=now, cash_per_trade=1_250.0,
        order_source="main_signal", bracket=dict(BRACKET), whole_shares=True,
        fill_at_next_open=True)


def test_a_halt_leaves_the_legs_armed_and_the_reopen_gap_is_honoured():
    data = {"AAPL": [_bar("2026-03-02", 100, 102, 99, 101),
                     # Tue-Thu: halted, no bars. Friday reopens far lower.
                     _bar("2026-03-06", 80, 82, 78, 81)]}
    emu = _emulator()
    _enter(emu, _at("2026-03-02"))
    _tick(emu, data, _at("2026-03-03"))
    for day in ("2026-03-04", "2026-03-05", "2026-03-06"):
        assert _tick(emu, data, _at(day)) == []
        assert emu.has_bracket_legs() is True
    [stop] = _tick(emu, data, _at("2026-03-09"))
    assert stop.source.startswith("bracket_sl_gap:")
    assert stop.price < 80.0                      # the open, less the spread
    assert stop.quote_timestamp == datetime(2026, 3, 6, 14, 30, tzinfo=UTC)


def test_bars_that_arrive_together_are_replayed_in_order():
    """Decided Friday; the Tuesday tick is skipped (Monday is Presidents'
    Day). Wednesday's tick sees Friday AND Tuesday: the entry fills at
    FRIDAY's open and Tuesday's low stops it out."""
    data = {"AAPL": [_bar("2026-02-12", 100, 101, 99, 100),
                     _bar("2026-02-13", 101, 103, 100, 102),
                     _bar("2026-02-17", 99, 100, 93, 95)]}
    emu = _emulator()
    _enter(emu, _at("2026-02-13"))
    buy, stop = _tick(emu, data, _at("2026-02-18"))
    assert buy.quote_timestamp == datetime(2026, 2, 13, 14, 30, tzinfo=UTC)
    assert stop.source.startswith("bracket_sl:")
    assert stop.quote_timestamp == datetime(2026, 2, 17, 21, 0, tzinfo=UTC)
```

- [ ] **Step 4: Run them to verify they fail**

Run: `python3 -m pytest backend/tests/test_backtest_bar_events.py backend/tests/test_swing_port_backtest_scenarios.py -q -p no:cacheprovider`
Expected: collection errors, `ModuleNotFoundError: No module named 'backtest_bar_events'`.

- [ ] **Step 5: Implement the module**

Create `backend/backtest_bar_events.py`:

```python
"""Bars for next-open fills and bracket legs in equity backtests (spec 6.2).

The simulator fills a `fill_at_next_open` order at a bar's OPEN and checks
bracket legs against each bar's HIGH and LOW. The broker's pending-fill block
only ever hands it the latest CLOSE, so this module supplies the rest: every
completed bar a symbol still needs, oldest first, each stamped with when it
opened and when it became known. It also turns a strategy's sizing hint into
the three `execute_signal` keywords, and only when the hint sets them.

Import-safe on purpose, like `backtest_price_history`: broker.py cannot be
imported under pytest, so the logic lives here and broker.py keeps thin
wrappers.
"""

from __future__ import annotations

import bisect
from datetime import datetime, timedelta, timezone
from typing import Callable

from event_time import aware_utc


#: symbol -> ((id(list), len(list)), labels, source indices). Labels are
#: parsed once per bar list; a list that grows or is replaced is re-parsed.
_LABELS: dict[str, tuple] = {}


def reset_label_cache() -> None:
    """Drop parsed bar labels. Tests call it; production never needs to."""
    _LABELS.clear()


def execution_hint_kwargs(nexus_hint, decision) -> dict:
    """The swing-port `execute_signal` keywords a sizing hint asks for.

    Returns {} unless the hint SETS `fill_at_next_open`, `whole_shares` or
    `bracket` -- so every other strategy's call is exactly what it was.
    `bracket` and `whole_shares` apply to buys only. A `bracket` that is not a
    mapping raises: silently buying without the stop it asked for is worse
    than a loud failure. Its prices are validated by `SimulationOrder`.
    """
    if not isinstance(nexus_hint, dict) or decision not in (1, -1):
        return {}
    out = {}
    if nexus_hint.get("fill_at_next_open") is True:
        out["fill_at_next_open"] = True
    if decision == 1:
        if nexus_hint.get("whole_shares") is True:
            out["whole_shares"] = True
        bracket = nexus_hint.get("bracket")
        if bracket is not None:
            if not isinstance(bracket, dict):
                raise ValueError("bracket hint must be a mapping")
            out["bracket"] = {
                "take_profit_price": bracket.get("take_profit_price"),
                "stop_loss_price": bracket.get("stop_loss_price"),
            }
    return out


def equity_daily_session_open(bar_start: datetime):
    """The NYSE session open for a daily bar's date (aware UTC), or None.

    The open-side twin of broker.py's `_equity_daily_bar_session_close`, and
    None under the same conditions: no calendar library, or not a session.
    """
    try:
        import pandas as pd
        import live_calendar

        calendar = getattr(live_calendar, "_CAL", None)
        if not bool(getattr(live_calendar, "_HAS_LIB", False)) or calendar is None:
            return None
        session = pd.Timestamp(bar_start.date())
        if not bool(calendar.is_session(session)):
            return None
        return calendar.session_open(session).tz_convert("UTC").to_pydatetime()
    except Exception:
        return None


def make_bar_open_resolver(
    *,
    interval: timedelta,
    session_open_resolver: Callable[[datetime], datetime | None] | None = None,
) -> Callable[[dict], datetime]:
    """bar -> the instant its first trade could print, aware UTC.

    A daily equity bar is labelled at midnight ET but opens at 09:30 ET; a
    decision at 05:00 PT is BEFORE that open, so the label would put the next
    open a whole session late. Intraday bars open at their label.
    """
    if not isinstance(interval, timedelta) or interval <= timedelta(0):
        raise ValueError("interval must be a positive timedelta")

    def _resolve(bar: dict) -> datetime:
        start = aware_utc(bar.get("t"), field="bar.t")
        if interval >= timedelta(days=1) and session_open_resolver is not None:
            opened = session_open_resolver(start)
            if opened is None:
                raise ValueError("exchange session open is unavailable")
            return aware_utc(opened, field="session_open")
        return start

    return _resolve


def _labels(symbol, bars, bar_time_to_datetime):
    key = (id(bars), len(bars))
    cached = _LABELS.get(symbol)
    if cached is not None and cached[0] == key:
        return cached[1], cached[2]
    labels, index = [], []
    for position, bar in enumerate(bars):
        label = bar_time_to_datetime((bar or {}).get("t"))
        if label is None:
            continue
        if label.tzinfo is None:
            label = label.replace(tzinfo=timezone.utc)
        labels.append(label.astimezone(timezone.utc))
        index.append(position)
    _LABELS[symbol] = (key, labels, index)
    return labels, index


def collect_bar_events(
    data: dict,
    requirements: dict,
    clock,
    *,
    bar_time_to_datetime: Callable,
    bar_available_at: Callable,
    bar_open_at: Callable,
) -> dict[str, list[dict]]:
    """Every completed bar each symbol still needs, oldest first.

    ``requirements`` is `PortfolioEmulator.bar_event_requirements()`:
    {symbol: earliest bar_ts needed}. A bar is returned when it opened at or
    after that time and is fully known by ``clock`` -- ALL such bars, not just
    the latest, so a weekend, a holiday or a skipped tick loses nothing. A bar
    whose times or prices cannot be read is skipped, as the price cursor
    skips it. A symbol with no such bar (a halt, a delisting) is absent.
    """
    now = aware_utc(clock, field="clock")
    out: dict[str, list[dict]] = {}
    for symbol in sorted(requirements or {}):
        since = aware_utc(requirements[symbol], field="since")
        bars = (data or {}).get(symbol) or []
        if not bars:
            continue
        labels, index = _labels(symbol, bars, bar_time_to_datetime)
        # A bar opens at or after its label and less than a day later.
        start = bisect.bisect_left(labels, since - timedelta(days=1))
        rows = []
        for position in index[start:]:
            bar = bars[position]
            try:
                available = aware_utc(bar_available_at(bar),
                                      field="bar_available_at")
            except (TypeError, ValueError):
                continue
            if available > now:
                break
            try:
                opened = aware_utc(bar_open_at(bar), field="bar_open_at")
                o, h, l, c = (float(bar[k]) for k in ("o", "h", "l", "c"))
            except (KeyError, TypeError, ValueError):
                continue
            if opened < since:
                continue
            rows.append({"t": bar.get("t"), "o": o, "h": h, "l": l, "c": c,
                         "bar_ts": opened, "available_at": available})
        if rows:
            out[symbol] = rows
    return out
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_backtest_bar_events.py backend/tests/test_swing_port_backtest_scenarios.py backend/tests/test_swing_port_invariance.py backend/tests/test_prices_cursor.py -q -p no:cacheprovider`
Expected: all pass (18 + 2 new).

- [ ] **Step 7: Check scope, then commit**

```bash
git add backend/backtest_bar_events.py backend/tests/test_backtest_bar_events.py backend/tests/test_swing_port_backtest_scenarios.py
```

Run `mcp__gitnexus__detect_changes({scope: "staged", repo: "IntelliStock"})`. Expected: new symbols in the new module only.

```bash
git commit -F - <<'EOF'
feat(backtest): collect every bar a next-open order or bracket leg needs

The new import-safe module backtest_bar_events turns a strategy's sizing
hint into execute_signal keywords, and returns nothing unless the hint
sets one. It resolves a daily bar's open to the NYSE session open. It also
collects every completed bar each symbol still needs, oldest first, so a
holiday or a skipped tick loses nothing and a halted symbol is simply
absent. The scenario tests replay the broker's own tick order on real
sessions.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 7: broker.py wiring, the deploy-check lists, and the full suite

**Files:**
- Modify: `backend/broker.py`:
  - `_submit_portfolio_signal` (12354-12373)
  - the import block (12703-12704)
  - two new helpers after `get_price_history_up_to_current` (12712-12725)
  - the bar hook after `_reconcile_anchor_pending_orders(portfolio_emulator)` (14565)
  - the main backtest submit (19075-19084)
- Modify: `backend/api/main.py` (`_CODE_FINGERPRINT_FILES`, around 1089) and `scripts/check_deployed_code.py` (`FILES`, around line 39)
- Test: `backend/tests/test_swing_port_broker_wiring.py`

**Interfaces:**
- Consumes: Task 5's `PortfolioEmulator.has_bracket_legs()`, `.has_next_open_orders()`, `.bar_event_requirements()` and `.process_bar_events()`. Task 6's `backtest_bar_events.*`.
- Produces:
  - `_submit_portfolio_signal(..., execution_hints=None)`
  - `_backtest_bar_open_resolver() -> Callable[[dict], datetime]`
  - `_process_backtest_bar_events(portfolio, data, prices, current_time) -> tuple[SimulationFill, ...]`

- [ ] **Step 1: Impact analysis.** broker.py is not indexed, so grep instead:

```bash
grep -n "_submit_portfolio_signal(\|_reconcile_anchor_pending_orders(portfolio_emulator)\|run_once_results = run_run_once_strategies(" backend/broker.py
```

Expected: `_submit_portfolio_signal(` at 5279, 5698, 12354 (def), 14686, 14705, 16234, 16255 and 19075. Only 19075 gains an argument; the new parameter defaults to `None` and adds nothing when empty. The anchor reconcile is at 14565 and the strategy call at 15506.

Run `mcp__gitnexus__impact` upstream on `_code_fingerprint` (`backend/api/main.py`) for the fingerprint-list change. Report: the bar hook runs on every equity backtest tick, but it is inert unless the emulator has legs or next-open orders (Task 5's `test_a_run_without_bar_work_never_needs_the_bar_hook`).

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_swing_port_broker_wiring.py`:

```python
"""broker.py wiring for next-open fills and bracket legs (spec 6.2).

broker.py is not import-safe (argparse and the main loop run at import), so
the functions under test are AST-extracted into a namespace that stubs what
they read, as test_strategy_x_broker_coexistence does. `_EXTRACTED` lists
them; `test_every_name_the_extracted_helpers_read_is_provided` fails, naming
the name, the day one of them starts reading an unstubbed global.
"""
from __future__ import annotations

import ast
import builtins
import datetime as datetime_module
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import backtest_bar_events as bbe  # noqa: E402
from bar_time import bar_time_to_datetime  # noqa: E402
from event_time import make_bar_availability_resolver  # noqa: E402
from portfolio_emulator import PortfolioEmulator  # noqa: E402
from simulated_execution import (  # noqa: E402
    ExecutionCostModel,
    NextEventExecutionSimulator,
)

BROKER_PATH = Path(__file__).resolve().parents[1] / "broker.py"
UTC = timezone.utc

_EXTRACTED = {
    "_submit_portfolio_signal",
    "_backtest_bar_open_resolver",
    "_process_backtest_bar_events",
}


def _session_close(bar_start):
    import live_calendar
    import pandas as pd
    session = pd.Timestamp(bar_start.date())
    if not live_calendar._CAL.is_session(session):
        return None
    return live_calendar._CAL.session_close(session).tz_convert(
        "UTC").to_pydatetime()


AVAILABLE = make_bar_availability_resolver(
    interval=timedelta(days=1), session_close_resolver=_session_close)


def _namespace():
    tree = ast.parse(BROKER_PATH.read_text())
    nodes = [n for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name in _EXTRACTED]
    assert {n.name for n in nodes} == _EXTRACTED
    applied, logged = [], []
    namespace = {
        "datetime": datetime_module,
        "_bbe": bbe,
        "_bar_time_to_datetime": bar_time_to_datetime,
        "_backtest_bar_interval": lambda: timedelta(days=1),
        "_aware_backtest_clock": (
            lambda t: t.replace(tzinfo=UTC) if t.tzinfo is None
            else t.astimezone(UTC)),
        "_backtest_bar_availability_resolver": lambda: AVAILABLE,
        "_backtest_fill_snapshot_marks": (
            lambda portfolio, prices, data, now: dict(prices or {})),
        "_apply_backtest_confirmed_fill_state": (
            lambda fill, marks: applied.append((fill, marks))),
        "_log": lambda message, color=None: logged.append(message),
        "applied": applied,
        "logged": logged,
    }
    for node in nodes:
        exec(compile(ast.Module([node], []), str(BROKER_PATH), "exec"),
             namespace)
    return namespace


def _free_names(function_name):
    tree = ast.parse(BROKER_PATH.read_text())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == function_name)
    bound = {a.arg for a in list(fn.args.args) + list(fn.args.kwonlyargs)}
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    loads = {n.id for n in ast.walk(fn)
             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return loads - bound - set(dir(builtins))


def test_every_name_the_extracted_helpers_read_is_provided():
    namespace = _namespace()
    for name in sorted(_EXTRACTED):
        missing = sorted(_free_names(name) - set(namespace))
        assert not missing, f"{name} reads unstubbed names: {missing}"


class _Recorder:
    has_next_event_execution = True

    def __init__(self):
        self.calls = []

    def execute_signal(self, ticker, signal, price, **kwargs):
        self.calls.append(kwargs)
        return True


@pytest.mark.parametrize("hints", [None, {}])
def test_an_eb_style_submission_passes_exactly_what_it_did(hints):
    ns = _namespace()
    portfolio = _Recorder()
    ns["_submit_portfolio_signal"](
        portfolio, "TQQQ", 1, 50.0, timestamp="t", cash_per_trade=1234.5,
        sell_fraction=1.0, order_source="main_signal",
        execution_hints=bbe.execution_hint_kwargs({"buy_cash": 1234.5}, 1)
        if hints is None else hints)
    assert portfolio.calls == [{"timestamp": "t", "cash_per_trade": 1234.5,
                                "sell_fraction": 1.0,
                                "order_source": "main_signal"}]


def test_a_swing_submission_forwards_the_hints():
    ns = _namespace()
    portfolio = _Recorder()
    hints = bbe.execution_hint_kwargs(
        {"buy_cash": 1250.0, "whole_shares": True, "fill_at_next_open": True,
         "bracket": {"take_profit_price": 109.0, "stop_loss_price": 94.0}}, 1)
    ns["_submit_portfolio_signal"](
        portfolio, "AAPL", 1, 100.0, timestamp="t", cash_per_trade=1250.0,
        order_source="main_signal", execution_hints=hints)
    [kwargs] = portfolio.calls
    assert kwargs["whole_shares"] is True
    assert kwargs["fill_at_next_open"] is True
    assert kwargs["bracket"] == {"take_profit_price": 109.0,
                                 "stop_loss_price": 94.0}


def test_a_legacy_emulator_never_sees_the_hints():
    ns = _namespace()
    portfolio = _Recorder()
    portfolio.has_next_event_execution = False
    ns["_submit_portfolio_signal"](
        portfolio, "BTC/USD", 1, 100.0, timestamp="t",
        order_source="main_signal", execution_hints={"whole_shares": True})
    assert portfolio.calls == [{"timestamp": "t", "cash_per_trade": 1000.0,
                                "sell_fraction": 1.0}]


def _daily(date, o, h, l, c):
    return {"t": f"{date}T05:00:00Z", "o": o, "h": h, "l": l, "c": c, "v": 1}


def test_the_bar_hook_fills_at_the_session_open_and_reports_each_fill():
    bbe.reset_label_cache()
    ns = _namespace()
    emu = PortfolioEmulator(
        10_000.0,
        execution_simulator=NextEventExecutionSimulator(ExecutionCostModel(
            version="test-v1", spread_bps=20.0, slippage_bps=10.0,
            fee_bps=5.0, latency=timedelta(0))),
        execution_delay=timedelta(days=1))
    monday_tick = datetime(2026, 3, 2, 13, 0)             # naive UTC clock
    emu.execute_signal("AAPL", 1, 100.0, timestamp=monday_tick,
                       cash_per_trade=1_250.0, order_source="main_signal",
                       whole_shares=True, fill_at_next_open=True,
                       bracket={"take_profit_price": 109.0,
                                "stop_loss_price": 94.0})
    data = {"AAPL": [_daily("2026-02-27", 99, 100, 98, 100),
                     _daily("2026-03-02", 100, 101, 93, 95),
                     _daily("2026-03-03", 95, 96, 94.5, 95)]}
    fills = ns["_process_backtest_bar_events"](
        emu, data, {"AAPL": 95.0}, datetime(2026, 3, 3, 13, 0))
    assert [(f.side, f.quote_timestamp) for f in fills] == [
        ("buy", datetime(2026, 3, 2, 14, 30, tzinfo=UTC)),
        ("sell", datetime(2026, 3, 2, 21, 0, tzinfo=UTC)),
    ]
    assert fills[1].source.startswith("bracket_sl:")
    assert [fill for fill, _marks in ns["applied"]] == list(fills)
    assert ns["applied"][0][1] == {"AAPL": 95.0}
    assert "exit_reason=stop_loss" in ns["logged"][1]
    # Tuesday is not complete at Tuesday 05:00 PT: nothing more, no error.
    assert ns["_process_backtest_bar_events"](
        emu, data, {}, datetime(2026, 3, 3, 13, 0)) == ()


def test_the_bar_hook_is_a_no_op_while_a_symbol_has_no_new_bar():
    bbe.reset_label_cache()
    ns = _namespace()
    emu = PortfolioEmulator(
        1_000.0,
        execution_simulator=NextEventExecutionSimulator(ExecutionCostModel(
            version="test-v1", spread_bps=0.0, slippage_bps=0.0,
            fee_bps=0.0, latency=timedelta(0))),
        execution_delay=timedelta(days=1))
    emu.execute_signal("HALT", 1, 10.0, timestamp=datetime(2026, 3, 2, 13, 0),
                       cash_per_trade=100.0, order_source="main_signal",
                       fill_at_next_open=True)
    assert ns["_process_backtest_bar_events"](
        emu, {"HALT": [_daily("2026-02-27", 10, 10, 10, 10)]}, {},
        datetime(2026, 3, 9, 13, 0)) == ()
    assert emu.pending_execution_symbols() == ("HALT",)


# -- source-level placement: the main loop cannot be executed in a test ------

_SOURCE = BROKER_PATH.read_text()


def test_the_bar_hook_runs_after_pending_fills_and_before_the_strategies():
    call = _SOURCE.index("_process_backtest_bar_events(\n"
                         "                    portfolio_emulator, data, prices, current_time)")
    assert _SOURCE.index("_reconcile_anchor_pending_orders(portfolio_emulator)") < call
    assert call < _SOURCE.index("run_once_results = run_run_once_strategies(")
    guard = _SOURCE.rindex("if (portfolio_emulator.has_bracket_legs()", 0, call)
    assert call - guard < 200
    # Defined before the module-level loop reaches it.
    assert _SOURCE.index("def _process_backtest_bar_events(") < call
    assert _SOURCE.index("import backtest_bar_events as _bbe") < call


def test_only_the_main_backtest_submission_forwards_hints():
    assert _SOURCE.count("execution_hints=_bbe.execution_hint_kwargs(") == 1
    site = _SOURCE.index("execution_hints=_bbe.execution_hint_kwargs(")
    assert _SOURCE.rindex("_mpg_result = _submit_portfolio_signal(", 0, site) > \
        _SOURCE.rindex("_anchor_order_source = (", 0, site)


def test_the_deploy_check_hashes_the_backtest_engine_files():
    """Spec 12: a push that changes only the simulator must not read as
    deployed before the image carrying it exists."""
    checked = (BROKER_PATH.parents[1] / "scripts"
               / "check_deployed_code.py").read_text()
    served = (BROKER_PATH.parent / "api" / "main.py").read_text()
    for rel in ("simulated_execution.py", "portfolio_emulator.py",
                "backtest_bar_events.py"):
        assert f'"backend/{rel}"' in checked
        assert f'    "{rel}",' in served
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_port_broker_wiring.py -q -p no:cacheprovider`
Expected: FAIL. Most tests fail at `assert {n.name for n in nodes} == _EXTRACTED` (the two helpers do not exist yet), the source-level tests fail with `ValueError: substring not found` / `assert 0 == 1`, and the deploy-check test fails on the missing entries.

- [ ] **Step 4: Forward hints through `_submit_portfolio_signal`**

In `backend/broker.py`, replace:

```python
    order_source,
):
    """Submit with source provenance only when using next-event execution."""
    kwargs = {
        "timestamp": timestamp,
        "cash_per_trade": cash_per_trade,
        "sell_fraction": sell_fraction,
    }
    if bool(getattr(portfolio, "has_next_event_execution", False)):
        kwargs["order_source"] = order_source
    return portfolio.execute_signal(ticker, signal, price, **kwargs)
```

with:

```python
    order_source,
    execution_hints=None,
):
    """Submit with source provenance only when using next-event execution.

    ``execution_hints`` is `backtest_bar_events.execution_hint_kwargs(...)`:
    empty unless a strategy's sizing hint set bracket / whole_shares /
    fill_at_next_open, so every other call passes exactly what it did.
    """
    kwargs = {
        "timestamp": timestamp,
        "cash_per_trade": cash_per_trade,
        "sell_fraction": sell_fraction,
    }
    if bool(getattr(portfolio, "has_next_event_execution", False)):
        kwargs["order_source"] = order_source
        if execution_hints:
            kwargs.update(execution_hints)
    return portfolio.execute_signal(ticker, signal, price, **kwargs)
```

- [ ] **Step 5: Import the module and add the two helpers above the main loop**

In `backend/broker.py`, replace:

```python
import backtest_prices_cursor as _bprices_cursor
```

with:

```python
import backtest_prices_cursor as _bprices_cursor
import backtest_bar_events as _bbe
```

In `backend/broker.py`, replace:

```python
        bar_available_at=_backtest_bar_availability_resolver(
            for_history=True,
        ),
    )

print("Time Increment:", time_increment)
```

with:

```python
        bar_available_at=_backtest_bar_availability_resolver(
            for_history=True,
        ),
    )


def _backtest_bar_open_resolver():
    """When each fetched bar's first trade could print: the NYSE session open
    for a daily bar, the bar's own label for an intraday one (spec 6.2)."""
    interval = _backtest_bar_interval()
    return _bbe.make_bar_open_resolver(
        interval=interval,
        session_open_resolver=(
            _bbe.equity_daily_session_open
            if interval >= datetime.timedelta(days=1) else None),
    )


def _process_backtest_bar_events(portfolio, data, prices, current_time):
    """Next-open fills and bracket legs over every bar not yet processed.

    Called after the pending-fill block and before the strategy call, and only
    when the emulator has a bracket leg or a next-open order, so a run that
    never asked for either never reaches it (spec 6.2).
    """
    clock = _aware_backtest_clock(current_time)
    if clock is None:
        return ()
    bars = _bbe.collect_bar_events(
        data,
        portfolio.bar_event_requirements(),
        clock,
        bar_time_to_datetime=_bar_time_to_datetime,
        bar_available_at=_backtest_bar_availability_resolver(),
        bar_open_at=_backtest_bar_open_resolver(),
    )
    if not bars:
        return ()
    marks = _backtest_fill_snapshot_marks(portfolio, prices, data, current_time)
    fills = portfolio.process_bar_events(bars, clock)
    for fill in fills:
        try:
            _log(
                "[execution] FILL %s %s qty=%.8f cumulative=%.8f "
                "price=%.6f fees=%.6f quote=%s model=%s source=%s "
                "exit_reason=%s"
                % (
                    fill.side.upper(),
                    fill.symbol,
                    fill.incremental_quantity,
                    fill.cumulative_quantity,
                    fill.price,
                    fill.fees,
                    fill.quote_timestamp,
                    fill.cost_model_version,
                    fill.source,
                    fill.exit_reason or "-",
                ),
                "green",
            )
        finally:
            _apply_backtest_confirmed_fill_state(fill, marks)
    return tuple(fills)


print("Time Increment:", time_increment)
```

- [ ] **Step 6: Call the hook after the pending-fill block, only when there is bar work**

In `backend/broker.py`, replace:

```python
            _reconcile_anchor_pending_orders(portfolio_emulator)
```

with:

```python
            _reconcile_anchor_pending_orders(portfolio_emulator)
            # Swing port (spec 6.2): next-open fills and bracket legs, off
            # every bar since the last one processed. Only a run that has
            # submitted a bracket or a next-open order ever gets past this.
            if (portfolio_emulator.has_bracket_legs()
                    or portfolio_emulator.has_next_open_orders()):
                _process_backtest_bar_events(
                    portfolio_emulator, data, prices, current_time)
```

(That line occurs once, at 14565, inside `if mode == MODE_BACKTEST and portfolio_emulator is not None:`.)

- [ ] **Step 7: Forward the strategy's hint at the main backtest submit**

In `backend/broker.py`, replace:

```python
                                    order_source=_anchor_order_source,
                                )
                                _mpg_submit_ok = bool(_mpg_result)
```

with:

```python
                                    order_source=_anchor_order_source,
                                    execution_hints=_bbe.execution_hint_kwargs(
                                        nexus_hint, decision),
                                )
                                _mpg_submit_ok = bool(_mpg_result)
```

- [ ] **Step 8: Add the engine files to both fingerprint lists (spec §12)**

In `backend/api/main.py`, replace:

```python
    "price_utils.py",
```

with:

```python
    "price_utils.py",
    # The backtest engine itself (swing port, spec 12): a push that changes
    # only the simulator must not read as deployed before its image exists.
    "simulated_execution.py",
    "portfolio_emulator.py",
    "backtest_bar_events.py",
```

In `scripts/check_deployed_code.py`, replace:

```python
    "backend/price_utils.py",
```

with:

```python
    "backend/price_utils.py",
    # The backtest engine itself (swing port, spec 12): a push that changes
    # only the simulator must not read as deployed before its image exists.
    "backend/simulated_execution.py",
    "backend/portfolio_emulator.py",
    "backend/backtest_bar_events.py",
```

- [ ] **Step 9: Run the wiring tests and every test that pins broker.py source or the fingerprint lists**

Run: `python3 -m py_compile backend/broker.py && python3 -m pytest backend/tests/test_swing_port_broker_wiring.py backend/tests/test_anchor_execution_contract.py backend/tests/test_strategy_x_broker_coexistence.py backend/tests/test_strategy_hx_broker_wiring.py backend/tests/test_code_fingerprint.py -q -p no:cacheprovider`
Expected: all pass (10 new). `test_strategy_hx_broker_wiring.py::test_the_health_fingerprint_and_the_deploy_check_list_the_same_files` proves the two lists still match.

- [ ] **Step 10: Run the full suite and compare it with the Task 1 baseline**

```bash
python3 -m pytest backend/tests -q -p no:cacheprovider 2>&1 | tee "${TMPDIR:-/tmp}/swing-port-A-final.txt" | tail -3
grep -E "^(FAILED|ERROR) " "${TMPDIR:-/tmp}/swing-port-A-final.txt" | sed 's/ - .*//' | sort | diff "${TMPDIR:-/tmp}/swing-port-A-baseline-failures.txt" -
```

Expected: the `diff` prints nothing (the same 19 pre-existing failures), and the pass count is the baseline 7,565 plus the 86 tests this plan adds (4 + 14 + 10 + 17 + 11 + 18 + 2 = 76 in Tasks 1–6, plus 10 here). Any new failure is a regression: fix it before committing. Never adjust the golden.

- [ ] **Step 11: Check scope, then commit**

```bash
git add backend/broker.py backend/api/main.py scripts/check_deployed_code.py backend/tests/test_swing_port_broker_wiring.py
```

Run `mcp__gitnexus__detect_changes({scope: "staged", repo: "IntelliStock"})`. Expected: the `_CODE_FINGERPRINT_FILES` constant only, plus the unindexed broker.py and scripts file. Confirm by `git diff --cached --stat` that exactly these four files are staged, and that `AGENTS.md` and `CLAUDE.md` are not.

```bash
git commit -F - <<'EOF'
feat(backtest): wire next-open fills and bracket legs into the broker loop

The main backtest submit forwards a strategy's bracket, whole_shares and
fill_at_next_open hints, and only when the hint sets them. After the
pending-fill block and before the strategy call, a bar hook collects
every bar that a next-open order or an armed leg still needs, and the
emulator applies them. It runs only when such work exists. Each fill is
logged with its source and exit reason and reconciled like any other.
The three engine files join both deploy-check fingerprint lists.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

- [ ] **Step 12: Hand-off note.** Do not push or merge from this plan. Record in the task summary that the post-deploy EB lab cold-start A/A backtest (spec §12, item 1) is the production-level check of the byte-identity this plan proves in unit tests.

---

## Self-review (run 2026-09-24, against the spec and the shared contract)

**1. Spec coverage**

| Spec requirement | Task |
|---|---|
| §6.2 order fields (`bracket`, `whole_shares`, `fill_at_next_open`) | 2 |
| §6.2 next-open fills at the first open after the decision, stamped at that open, market cost | 3 |
| §6.2 `_bracket_legs[parent_id]` with symbol, filled qty and absolute prices; kept out of `_pending`; no share reservation | 4 (and 5: legs never enter `_execution_position_reservations`) |
| §6.2 `on_bar(SimulationBarEvent)` via `process_bar_events`, from broker after the pending block, guarded | 3, 5, 7 |
| §6.2 the parent's fill bar is checked | 4 (`test_the_parents_own_fill_bar_is_checked`) |
| §6.2 the five trigger rules | 4 (parametrised, one case per rule) |
| §6.2 leg costs (stop: spread + slippage; target: fee only) | 4 |
| §6.2 one-cancels-other and cancelling pending strategy sells; a strategy sell shrinks the legs | 4, 5 |
| §6.2 fill sources and `exit_reason`; summary keys only when a bracket was submitted | 2, 4, 5 |
| §5.1 backtest items 4–5, the hint shapes | 6 (`execution_hint_kwargs`), 7 (forwarded at the main submit) |
| §11 non-bracket fills and `execution_summary` identical before and after | 1 (written first), then re-run in every task; 5 (explicit default keywords) |
| §11 simulator tests: triggers, OCO, next-open, whole shares, legs absent from pending counts | 3, 4, 5 |
| §11 EB path unaffected | 1, 5, 6 (EB hint shapes → `{}`), 7 (`_submit_portfolio_signal` kwargs unchanged) |
| §11 `test_strategy_x_broker_coexistence` sentinel lists | Unchanged: this plan does not modify `run_run_once_strategies` (plan A-live does). Task 7 runs that file. |
| §12 add the new files to the deploy check | 7 |

**2. Placeholder scan:** no "TBD", "TODO", "similar to" or undefined names. Every code step shows the exact text to find and its replacement. The one environment-dependent value, the golden hash, is given with an explicit rule for when it differs.

**3. Type consistency:**
- `on_bar(event, *, accept_fill, cash_budget, position_of)` is defined in Task 3 and called that way in Task 5.
- `bar_event_requirements()` returns aware datetimes in Tasks 3, 4 and 5, and is consumed by `collect_bar_events(requirements=...)` in Tasks 6 and 7.
- The bar dict keys `o/h/l/c/bar_ts/available_at` are produced in Task 6 and consumed in Task 5.
- `_due_next_open` (Task 3) is reused by `bar_event_priority` (Task 5).
- `exit_reason` values are `"stop_loss"` / `"take_profit"` everywhere.
- The sources match the contract's four strings.

**4. Review Focus:** all five lines have a pinning test in their owning task (listed with each line above). This plan was also checked mechanically on 2026-09-24:
- A script applied every task's `Create` and `replace` blocks, in order, to a `git archive` copy of HEAD 299d551, and ran every `Run:` step. Every "fails" step failed, every "passes" step passed, and the golden hash matched.
- The full `backend/tests` suite then ran on that copy before and after: 7,502 → 7,588 passed (+86, this plan's tests), with an **identical** set of failing test ids. The copy has 82 failures, against the repo's 19, because `git archive backend scripts` omits the other top-level directories some tests read.
