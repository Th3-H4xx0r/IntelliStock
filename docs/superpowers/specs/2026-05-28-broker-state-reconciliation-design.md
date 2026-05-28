# Broker State Reconciliation — WAL-anchored clean-room boot for live trading

**Date:** 2026-05-28
**Status:** Approved (brainstorm → spec)
**Branch:** claude-code-integration
**Target instance:** `main` (Robinhood live), generalizable to all live instances
**Related:** prior handoffs on kimi rotation-override recalibration (orthogonal); MEMORY: live-broker-adapters-adopt-existing-broker-state-at-startup, instance-broker-labels-misleading
**Supersedes:** none

## 1. Problem

The IntelliStock live broker adapters (`AlpacaAdapter`, `RobinhoodAdapter`) **blindly adopt all brokerage account state at boot** via their `__init__`:

- `refresh_cash()` → `self._cash`
- `refresh_positions()` → `self._positions` (every position in the account)
- `_seed_trades_from_broker(limit=500/200)` → `self._trades` (every recent fill, regardless of provenance)
- `refresh_account()` → `self._initial_value = acct_dto.equity`

This is the **single sharpest contamination vector** for live trading. Any of the following pollute the strategy's runtime view:

- Manually-placed Robinhood orders made for testing or personal trading.
- Positions left over from prior strategy versions or aborted experiments.
- Recent fills whose `client_order_id` was never written by this strategy.

Downstream effects in `backend/strategies/graph_nexus_analysis.py`:

- `_get_open_position_entry_trade` (line 5123) walks `_trades` chronologically and treats the first BUY after qty went to zero as the entry. A stale broker fill becomes the synthetic "entry price" → V32 trailing stop, hold-limit, fast-loser-cut, mega-winner-protect, and V28 rotation `days_held` all compute against the wrong reference.
- `initial_value` (line 18940) is used as the drawdown-peak baseline → an account with bad-test PnL boots with the wrong peak.
- `_cash_floor = _initial_value * _cash_reserve_floor_pct` (broker.py:8751) → wrong floor.
- `_get_scaled_max_new_stock_buys(config, initial_value)` → may pick the wrong size tier.

A naive fix ("ignore broker state at boot") would break **restart continuity**: the strategy needs to recognize positions it bought itself in a prior cycle. The operator explicitly raised this case — *"if the strategy bought stuff and then I end up stopping the instance and start it up again, will this cause issues?"*

So the real problem is not *"fresh vs restart"* — it is *"is broker state ours or external?"*. The system needs a **provenance signal** that survives across restarts.

## 2. Goals

- **G1** Eliminate broker-side contamination of strategy state for live mode (positions, trade history, initial_value, drawdown baseline).
- **G2** Preserve restart continuity: a daemon stop+start within seconds (or hours, or days) of the strategy's last activity must resume with the same positions, entry prices, and days-held that the strategy was managing.
- **G3** Quarantine external positions (positions in the broker that the strategy did not place) into a separate dict so the strategy never sells them, but log them prominently so the operator knows they are there.
- **G4** Provide a forensic audit row per boot recording exactly what was adopted, what was quarantined, and the source of each piece of state.
- **G5** Preserve all useful real-time broker queries during runtime (live cash, fill events via trade-updates stream, account flags, quotes). Clean-room mode is about *boot-time adoption*, not about *runtime data*.
- **G6** Backward compatible: existing live deployments must continue working without code-side changes unless they opt in.

## 3. Non-Goals

- Changing the broker adapter ABC or any external-facing strategy interface.
- Replacing the existing `reconcile_wal_with_broker()` logic (it's correct; we extend it).
- Auto-flattening or auto-cancelling existing broker positions/orders (operator action).
- Per-fill broker data syncing during steady-state (covered by the existing trade-updates stream + REST refresh paths; out of scope).
- Multi-broker portfolios on a single instance.

## 4. Background — primitives we build on

| Primitive | Location | What it provides |
|---|---|---|
| **LiveOrderWAL** | global RethinkDB table; writer in `backend/broker_adapters/_wal.py` | Every order intent submitted by any strategy/instance has a row with our generated `client_order_id`. After fill, the row carries `broker_order_id`, `filled_qty`, `filled_avg_price`, and a terminal `status` (`filled`/`partial`/`canceled`/`rejected`). **This is the provenance anchor.** |
| **NexusStrategyCache snapshot** | per-instance rows, origin=`live` for ongoing instances + origin=`backtest` for pre-launch snapshots; persisted in `backend/strategy_cache_persistence.py` and consumed at `broker.py:5347-5421` | Carries `_v32_entry_prices`, `_v32_position_history`, `_deployment_bar_index`, `_portfolio_drawdown_state` (incl. `peak_value`), `_sold_cooldown`, `_overlay_no_data_tickers`. Already hydrated at boot. |
| **`reconcile_wal_with_broker()`** | `alpaca.py:1669`, `robinhood.py:2506`; runs at boot from `broker.py:5292-5298` | Settles non-terminal WAL rows by querying the broker by `client_order_id`. Updates WAL row state to terminal. Currently only handles non-terminal rows; the design extends it to also reconstruct `_trades` from terminal rows. |
| **`seed_trades_from_broker: bool = True`** constructor param | `alpaca.py:138`, `robinhood.py:167` | Already plumbed; gates `_seed_trades_from_broker()`. No caller currently passes False. Used as the lever to disable the contaminating bulk-fetch. |
| **`initial_value: float | None = None`** constructor param | `alpaca.py:137`, `robinhood.py:166`; plumbed through `factory.py:18-73` | Already plumbed; never passed by `broker.py:5259-5276`. Used as the lever to inject an explicit baseline that bypasses broker equity. |
| **Client Order ID prefix** | `backend/broker_adapters/_client_order_id.py` | Unique per-strategy-submitted order. Tags WAL rows + broker orders. Manually-placed Robinhood orders never carry this prefix → instantly distinguishable. |

## 5. Design — Approach A: WAL+Snapshot reconciliation, auto-detect

### 5.1 High-level flow at boot

```
broker.py boot (instance=main, broker_type=robinhood):

 1.  Resolve clean_room_mode + initial_value
       from Instances row fields and/or env vars
       (default: clean_room_mode=False for backward compat;
        operator opts in for `main`)

 2.  Build adapter via factory:
       _build_adapter(..., clean_room_mode=True, initial_value=$X)

 3.  Adapter __init__:
       a. refresh_cash()           — broker is authoritative for cash
       b. refresh_positions()      — pull ALL broker positions (we always need this)
       c. Open WAL connection
       d. classify_broker_positions(positions, wal):
            - For each broker position, look up the most recent
              filled WAL row (by ticker + client_order_id prefix
              matching THIS instance) within retention window.
            - Match  → strategy-owned; record (entry_price, entry_ts,
                       broker_order_id, client_order_id)
            - No match → external; record (qty, market_value, "no WAL trace")
       e. self._positions          = strategy-owned dict[ticker -> qty]
          self._external_positions = external dict[ticker -> {qty, mv, note}]
          self._trades             = reconstructed from filled WAL rows
                                     for THIS instance (not blanket
                                     _seed_trades_from_broker)
       f. self._initial_value = explicit param  OR  snapshot's recorded
                                value  OR  raise BrokerError
       g. Persist a LiveBootAudit row.
       h. If external positions exist: log YELLOW + Discord alert
          via _alert_strategy_error.

 4.  Continue normal boot (reconcile_wal_with_broker for any
     still-non-terminal WAL rows; refresh_orders_today; snapshot
     hydrate; F1b ramp bypass; etc.)
```

When `clean_room_mode=False` the adapter behavior is unchanged.

### 5.2 Classification semantics

A broker position for ticker `T` is classified **strategy-owned** if and only if:

1. There is a WAL row `w` such that
   - `w.ticker == T`
   - `w.status == "filled"` (or `"partial"` with `filled_qty > 0`)
   - `w.side == "BUY"`
   - `w.client_order_id` starts with this instance's client_order_id prefix
   - `w.filled_at_utc` is within the retention window AND is the most recent buy-fill for `T` before any intervening sell-to-zero
2. The cumulative net qty implied by all WAL rows for `T` matches the broker's reported `qty` (within float tolerance).

If condition 1 holds but condition 2 fails (broker qty != WAL-implied qty), classify as **partial-external**: strategy-owned for the WAL-implied qty; the excess qty is external. Log loud.

If condition 1 fails entirely → **external**.

### 5.3 Trade history reconstruction

In `clean_room_mode=True`, replace the blanket `_seed_trades_from_broker(limit=500)` call with a WAL-driven walk:

```
def _seed_trades_from_wal(self, wal: WALStore) -> None:
    rows = wal.list_terminal_rows(
        instance_id=self._instance_id,
        client_order_id_prefix=self._cid_prefix,
        since_utc=now - timedelta(days=180),  # generous; configurable
    )
    for row in rows:
        if row.status not in ("filled", "partial"):
            continue
        self._trades.append({
            "ticker": row.ticker,
            "action": row.side,
            "shares": row.filled_qty,
            "price": row.filled_avg_price,
            "timestamp": row.filled_at_utc,
            "client_order_id": row.client_order_id,
            "broker_order_id": row.broker_order_id,
            "source": "wal",
        })
    self._trades.sort(key=lambda t: t["timestamp"])
```

Strategy code that reads `_trades` (V32 trailing stop, days-held, etc.) is unchanged — it sees the same shape.

### 5.4 `_initial_value` resolution order

In `clean_room_mode=True`, resolve in this order; first non-None wins:

1. Explicit `initial_value` parameter passed to `__init__`.
2. `Instances.<instance_id>.initial_value` field (read at boot in broker.py).
3. Persisted strategy snapshot's `_initial_value` (from NexusStrategyCache).
4. `BrokerError("clean_room_mode=True requires an explicit initial_value")` — fail loud.

In `clean_room_mode=False` (legacy): unchanged — falls through to `refresh_account().equity`.

### 5.5 External-position quarantine

A new attribute on both adapters and the ABC:

```python
self._external_positions: dict[str, dict] = {
    "AAPL": {"qty": 50.0, "market_value": 9234.50, "first_seen_utc": ...,
              "note": "no WAL trace within retention window"},
    ...
}
```

Public accessors:

- `get_positions()` → unchanged; returns ONLY strategy-owned.
- `get_external_positions()` → NEW; returns `_external_positions`.

Strategy code (`graph_nexus_analysis.py`) does NOT read `_external_positions`. It will not be sized into, will not be candidate for rotation, will not be sold.

`refresh_positions()` re-classifies on every call so that operator-side mutations (e.g., manually liquidating an external in the Robinhood app) are reflected.

### 5.6 LiveBootAudit table

New RethinkDB table `LiveBootAudit`. One row per boot:

```
id            : "<instance_id>|<boot_ts_iso>"  (primary key)
instance_id   : str
boot_at_utc   : ISO timestamp
broker_type   : "alpaca" | "robinhood"
mode          : "clean_room" | "legacy"
broker_cash_at_boot                : float
broker_positions_total             : int
strategy_owned_count               : int
strategy_owned_tickers             : list[str]
external_count                     : int
external_tickers_qty               : dict[str, float]
initial_value                      : float
initial_value_source               : "explicit" | "instance_row" | "snapshot" | "broker_equity"
snapshot_loaded                    : bool
snapshot_keys                      : int
trades_seeded                      : int
trades_seeded_source               : "wal" | "broker_history" | "none"
notes                              : list[str]   # warnings, alerts fired, etc.
```

Indexed on `instance_id` and `boot_at_utc`. Cleared by the existing per-instance clear script (audit data is per-instance scoped).

### 5.7 Operator tooling

Two new scripts:

**`scripts/inspect_broker_state.py --instance <id>`**

Read-only pre-flight. Does NOT boot the broker. Instead:

1. Read the Instances row + BrokerageAccounts row → resolve broker type + credentials.
2. Construct an adapter in a special inspect-only mode (`dry_init=True`).
3. Call `refresh_cash()` + `refresh_positions()` + WAL-classify.
4. Print a structured report:

```
Instance: main  (broker: robinhood)
Brokerage account: REDACTED-ACCT
Cash at broker: $9,876.54
Positions at broker: 3 total
  - AAPL  50.0sh  mv=$9,234   matched WAL row cid=intellistock-main-20260520-1234 (BUY 50sh @ $184.70 on 2026-05-20)  →  STRATEGY-OWNED
  - TSLA  12.0sh  mv=$3,120   no matching WAL row                              →  EXTERNAL
  - NVDA   8.0sh  mv=$2,890   no matching WAL row                              →  EXTERNAL
Open orders: 0
LiveOrderWAL non-terminal rows for this instance: 0
Persisted snapshot: present  (initial_value=$10,000, last_persisted 2026-05-25)

Verdict: if you boot clean_room_mode=True, the strategy will adopt 1 position (AAPL)
and quarantine 2 positions (TSLA, NVDA). Manually liquidate TSLA/NVDA via the
Robinhood UI if you want them gone before boot.
```

**`scripts/migrate_external_position.py --instance <id> --ticker AAPL --action {adopt|ignore-permanently}`**

Explicit operator action. `adopt` writes a synthetic WAL row (status=`filled`, source=`migrated`) so the position becomes strategy-owned on next refresh. `ignore-permanently` writes to an `ExternalIgnoreList` table so audit log stops alerting.

### 5.8 Configuration surface

| Knob | Source | Default | Effect |
|---|---|---|---|
| `Instances.<id>.clean_room_mode` | DB field (bool) | not set → False | Enables clean-room boot for that instance |
| `Instances.<id>.initial_value` | DB field (float) | not set → None | Operator-supplied baseline; takes precedence over snapshot |
| `LIVE_CLEAN_ROOM_MODE` | env | not set → falls back to DB field | Per-host force-on (rare; mostly for ops) |
| `LIVE_INITIAL_VALUE` | env | not set → falls back to DB field | Per-host force-set |
| `LIVE_CLEAN_ROOM_WAL_RETENTION_DAYS` | env | `180` | How far back to walk WAL for classification |

`main` will be migrated to `clean_room_mode=True, initial_value=$<TBD by operator>`.

## 6. Failure modes

| Scenario | Behavior |
|---|---|
| `clean_room_mode=True` but no explicit `initial_value` AND no snapshot AND no `Instances.initial_value` | `BrokerError` at adapter init; broker.py catches it, fires `_alert_strategy_error`, `sys.exit(5)`. Loud, immediate, recoverable by setting the field. |
| WAL has no history (e.g., pruned, never written) and broker has positions | Every position classified external. Loud Discord alert. Operator either uses `migrate_external_position.py --action adopt` or accepts that the strategy starts position-free. |
| Snapshot is stale (last persisted > N days ago) | Snapshot still hydrated for non-position state (drawdown peak, sold_cooldown, etc.). `_initial_value` falls through to Instance row. Classifier still runs against WAL. Audit row notes stale snapshot. |
| Broker shows position X with qty 50 but WAL has filled buys totaling 30 (partial external mixed) | Strategy adopts 30sh as owned. 20sh quarantined as external. Detailed audit log line. |
| Broker shows position the strategy thinks it owns (in snapshot) but qty differs | `refresh_positions()` returns broker reality; snapshot is reconciled down. Strategy's `_v32_entry_prices` for that ticker remain valid for the WAL-implied portion. Discord alert if delta > threshold. |
| Broker shows fewer positions than the strategy thinks (manual sell during downtime) | Strategy's `_positions` reflects broker (authoritative). Snapshot's per-ticker entry caches are pruned. Discord alert: "operator-side liquidation detected: TSLA was held, now broker shows 0." |
| Classifier raises an exception | Caught at adapter init; logged; fall back to TREATING ALL POSITIONS AS EXTERNAL (safer default). Discord alert. Broker keeps running with empty `_positions`. |

## 7. Backward compatibility

- All new constructor params (`clean_room_mode`, `initial_value`) default to existing behavior.
- Adapters without the param set behave exactly as today.
- The `_external_positions` attr defaults to `{}`; ABC method `get_external_positions()` has a default implementation that returns `{}`.
- The new LiveBootAudit table auto-creates on first write; existing operations untouched.
- Existing per-instance clear script (`clear_main_instance_lookback_state.py`) gets one new table added (`LiveBootAudit`).
- The existing `_seed_trades_from_broker` method is preserved (used in `clean_room_mode=False`).

## 8. Testing strategy

### 8.1 Unit tests (in `backend/tests/`)

- `test_broker_clean_room_classify_strategy_owned.py` — synthetic broker positions + WAL rows; assert classifier returns correct strategy-owned set.
- `test_broker_clean_room_classify_external.py` — synthetic broker positions with NO matching WAL; assert all marked external.
- `test_broker_clean_room_classify_partial.py` — broker qty > WAL-implied; assert partial-external split.
- `test_broker_clean_room_initial_value_resolution.py` — exercise the 4-step resolution order.
- `test_broker_clean_room_wal_seed_trades.py` — assert `_trades` rebuilt only from this-instance WAL rows.
- `test_broker_clean_room_failure_modes.py` — exception in classifier → all-external fallback; missing initial_value → BrokerError.

### 8.2 Smoke / integration tests

- `test_alpaca_adapter_clean_room_init.py` — construct adapter with `clean_room_mode=True`, mock broker API, mock WAL; verify `_positions`, `_trades`, `_external_positions`, `_initial_value`.
- `test_robinhood_adapter_clean_room_init.py` — same shape, RH-specific mocks.
- `test_clean_room_legacy_behavior_preserved.py` — `clean_room_mode=False` → exactly current behavior.

### 8.3 Behavior tests (scenario-driven)

- `test_clean_room_first_boot_with_contaminated_broker.py` — broker has 3 positions, WAL empty → all 3 quarantined; `_positions={}`; audit row written.
- `test_clean_room_restart_continuity.py` — simulate prior cycle: WAL filled with BUY TSLA from this instance; restart → TSLA classified strategy-owned; entry price + days_held match.
- `test_clean_room_manual_sell_during_downtime.py` — snapshot says TSLA held; broker now shows 0 → strategy state reconciled to 0; alert fired.
- `test_clean_room_partial_external.py` — broker shows 50sh; WAL shows 30sh → strategy owns 30, external owns 20.

### 8.4 Baseline tests

- Full pytest run must show **exactly the 21 pre-existing failures** (the known baseline). Zero new failures.

### 8.5 Lint / typing

- mypy clean on new code.
- No new ruff warnings on touched files (modulo intentional).

## 9. Migration plan

After implementation lands:

1. Run full test suite locally; confirm 21 baseline failures only, no new ones.
2. Bug sweep (parallel agents).
3. Commit + push to `claude-code-integration`.
4. On the operator's side, when ready to deploy `main` live:
   a. Run `python scripts/inspect_broker_state.py --instance main` — get the pre-flight report.
   b. Manually flatten any external positions in the Robinhood UI.
   c. Manually clear LiveOrderWAL rows for `main` (per the existing live-launch-checklist.md).
   d. Run `python scripts/clear_main_instance_lookback_state.py --instance main --apply`.
   e. Write a merge-only apply script that sets `Instances.main.clean_room_mode=True` and `Instances.main.initial_value=$<operator's chosen starting capital>`.
   f. Run it `--apply`.
   g. Re-run `inspect_broker_state.py --instance main` to verify clean state.
   h. Start the broker daemon for `main`.

The apply script for step (e) is a separate deliverable, generated when the operator picks the capital number. It is NOT auto-applied as part of this implementation.

## 10. Open Questions

None blocking. The design fully addresses the stated requirements (contamination prevention + restart continuity + audit + safeguards for useful broker data) using existing primitives (WAL, snapshot persistence, dormant constructor parameters).

## 11. Related Patterns Extended

- The `seed_trades_from_broker: bool` constructor flag is generalized.
- The `initial_value: float | None` parameter (currently plumbed-but-unused) is activated.
- The `_alert_strategy_error` Discord channel is used for boot-time anomaly surfacing.
- The merge-only apply-script pattern (`apply_doc179_rotation_override_fix.py`) is the template for the future `main` migration script.
- The per-instance clear script (`clear_main_instance_lookback_state.py`) is extended by one table.

## 12. Risk Assessment

- **Code change blast radius:** moderate. Adapter constructors are touched, but the `clean_room_mode=False` path is unchanged. Existing live deployments (none currently running per operator confirmation) are unaffected unless they opt in.
- **Failure to detect strategy-owned positions** (false-external): operator alerted; can migrate via the script. No financial harm — strategy just doesn't manage them.
- **False-strategy-owned (external position incorrectly adopted):** would require a `client_order_id` collision with an externally-placed order using our exact CID prefix. The CID generator (`_client_order_id.py`) uses unique high-entropy components; collision probability is negligible.
- **Test coverage:** new code paths fully unit-tested; existing baselines preserved.
