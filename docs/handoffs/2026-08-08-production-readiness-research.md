# Production-readiness research — doc-193 → alpaca-main (2026-08-08)

Read-only research. No code changed, nothing pushed, no backtest started.
Every claim below is a quote or a line reference from the tree at
`/Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock` as of this session.

---

## 1. PIT MODE — where the `research` override happens, and what `strict` needs

### The override site (exactly one)

`backend/interactive_utils.py:5607-5608`, inside `action_create_backtest`:

```python
if not non_equity_compatibility and _evidence_in.get("pit_mode") is None:
    _evidence["pit_mode"] = "research"
```

`non_equity_compatibility` is `kind in {"crypto","kalshi"}` (`interactive_utils.py:5591-5592`),
so **every equities backtest** that does not name `pit_mode` gets `research`. The comment
above it (`:5599-5606`) says why it lives there rather than in the API: "Applied here rather
than in the API layer so every creation path inherits it — UI, CLI, chatbot, rerun script,
Discord bot."

`_evidence_in` is built from non-None values only (`interactive_utils.py:5587`), so a POST that
omits `pit_mode` (or sends `null`) hits the override.

### The value is not `'pit'` — it is `'strict'`

`backend/backtest_evidence_options.py:66`:

```python
PIT_MODES = frozenset({"strict", "research"})
```

`_validate_pit_mode` (`backtest_evidence_options.py:155-160`) returns `"strict"` for `None`
and rejects anything outside that set. There is no `'pit'` value anywhere in the tree.

### Is there an API field? Yes.

- `backend/api/main.py:678` — `pit_mode: Optional[str] = None  # strict (default) | research (declared lookahead bias)` on `CreateBacktestBody`.
- `backend/api/main.py:2997` — it is forwarded into `evidence_options` on `POST /backtests`.

So **no code change is required** to request strict: `POST /backtests` with
`{"pit_mode": "strict", ...}` is enough to skip the `interactive_utils.py:5607` override.
(With everything else default, no `evidence` block is even written to the row —
`interactive_utils.py:5673-5682` only stamps it when something is non-default — and the broker
re-validates an absent block to the all-default contract, which is `pit_mode="strict"`:
`broker.py:1452-1486`.)

**`scripts/run_validation_backtest.py` cannot do this today.** Its POST body is
`scripts/run_validation_backtest.py:39-46` — `instance_id, stocks, start_date, end_date,
granularity, initial_cash`. No evidence options at all.

### What a strict run then requires — and why it fails today

`backend/broker.py:5677-5793`, `_run_graph_nexus_with_point_in_time`. In backtest
(`mode == MODE_BACKTEST`) there are three branches:

1. config supplies all three of `point_in_time_manifest` / `point_in_time_store` /
   `point_in_time_session_close_resolver` (`broker.py:5678-5712`) — **nothing in the repo ever
   sets these keys on a backtest config**; the only writers I could find are tests
   (`backend/tests/test_broker_graph_nexus_pit.py:147`).
2. `pit_mode == "research"` → the legacy current-state path (`broker.py:5713-5760`).
3. otherwise → **strict**, `broker.py:5761-5774`:

```python
else:
    from point_in_time_registry import resolve_default_bundle

    bundle = resolve_default_bundle(as_of)
```

`resolve_default_bundle` is `RethinkPointInTimeRegistry().resolve_bundle(as_of)`
(`backend/point_in_time_registry.py:702-705`), and `resolve_bundle` raises when the registry
is empty (`point_in_time_registry.py:505-511`):

```python
record = self._resolve_manifest_record(as_of_utc)
if record is None:
    raise PointInTimeDataError(
        f"no finalized point-in-time manifest exists at or before {_iso_z(as_of_utc)}"
    )
```

A manifest also has to carry all four datasets — `REQUIRED_DATASETS = ("graph",
"fundamentals", "universe", "news")` (`point_in_time_registry.py:30`, enforced at `:328-333`).

Those manifests can only be produced by **live FULL ticks**. `_pit_capture_enabled`
(`backend/strategies/graph_nexus_analysis.py:6709-6720`):

```python
if context is None or not context.is_live:
    return False
if str(mode or "FULL").strip().upper() != "FULL":
    return False
```

`docs/runbooks/point-in-time-capture.md:17` states `PointInTimeManifests` and
`PointInTimeDatasetSnapshots` had **0 rows** as of 2026-08-03, and `:29-37` explains capture can
never come from a backtest, by design.

**Conclusion for Q1.** A strict backtest needs no code change and no new config key — just
`pit_mode: "strict"` on the POST (which `run_validation_backtest.py` would have to grow, ~2
lines). It will then fail at the first Graph Nexus decision with
`PointInTimeDataError: no finalized point-in-time manifest exists at or before …` until an
equities instance runs live FULL ticks with `PIT_CAPTURE_ENABLED=1` and populates the registry.
That is a **data** blocker, not a code one. Everything measured so far (+6.02% → +15.04%)
is `pit_mode=research`, i.e. carries lookahead bias and is stamped
`pit_provenance=legacy_unverified`, `promotion_eligible=False`
(`backend/backtest_evidence_runtime.py:326-331`; broker warning at `broker.py:9069-9075`).

---

## 2. VALIDATION HARNESS — `history_scope_salt` and clean paired A/B

### How the salt is consumed

Single consumption site: `backend/nexus_config_identity.py:127`, inside `history_scope_doc()`:

```python
"history_scope_salt": str(settings.get("history_scope_salt") or "").strip(),
```

`history_scope_doc` is the canonical doc whose sha256[:24] is the **`history_scope_id`**
(`nexus_config_identity.py:136-149`). An explicit `history_scope_id` in the config wins over
the computed one (`:143-145`).

The id becomes the runtime state namespace. `backend/broker.py:6165-6169`:

```python
def _resolve_nexus_runtime_identity(base_instance_id, settings):
    base = str(base_instance_id or "").strip() or "default"
    scope_id = _nexus_history_scope_id(settings)
    scoped_instance_id = f"{base}|{scope_id}" if scope_id else base
    return base, scope_id, scoped_instance_id, _nexus_history_scope_doc(settings)
```

and it is injected into the strategy config/conditions at `broker.py:5955-5968`
(`history_scope_id`, `runtime_instance_id = "alpaca-main|<scope_id>"`). Downstream:

- `graph_nexus_analysis.py:24547-24551` — `instance_id` for all per-instance Nexus state is
  `config["runtime_instance_id"]`, i.e. the **scoped** id.
- `broker.py:6306-6349` / `6650-6754` — the historic-lookback resume query is keyed on the
  scoped id; `broker.py:6894` filters `GraphNexusTradeContexts` on `history_scope_id`.
- `nexus_config_identity.py:53-61` — `history_scope_id_inputs` also feed the 16-char
  `live_config_hash` used for snapshot reuse.
- `graph_nexus_analysis.py:1700-1709` — the enhanced-sentiment LLM cache scope **includes
  `history_scope_id`**, so changing the salt also makes the arm cold in the sentiment cache
  (extra LLM cost; it is not a free flag).

### Is it a strategy-config key? Yes — and only that.

It is read out of the merged strategy settings (`broker.py:6146-6151`, `5948-5954`), i.e. it
lives in `Strategies.<doc>.strategies[0].config.history_scope_salt`. Evidence from disk:
`scripts/doc193_backup_patch_20260808T011456Z.json` → `strategies[0].config.history_scope_salt
= "let-run-core-193"`; the older doc-179 arms used `"iso-2026-07-30-a1"` / `"iso-2026-07-30-a2"`
(`scripts/doc179_backup_patch_20260730T184410Z.json`, `…204353Z.json`).

**It is not settable per backtest run.** The only per-run strategy overrides allowed on
`POST /backtests` are the four A1–A4 keys in `CANDIDATE_OVERRIDE_KEYS`
(`backtest_evidence_options.py:42-47`), and `history_scope_salt` is not one of them; anything
else raises `"… is not an approved candidate override"` (`:148-150`).

### Does `scripts/run_validation_backtest.py` support it?

**No.** It posts only `instance_id / stocks / start_date / end_date / granularity /
initial_cash` (`scripts/run_validation_backtest.py:39-46`). There is no salt, no evidence
option, no config patching.

The pattern that exists for A/B config patching is `scripts/apply_doc179_config_patch_api.py`
(GET `/strategies/179` → mutate only the named keys inside the `graph_nexus_analysis` config →
PUT back, with a timestamped backup), driven by `scripts/validate_bull_candidate.py`
(patch → run → patch → run → revert). `scripts/apply_doc193_*.py` do the same directly against
RethinkDB for doc-193.

### Minimal correct procedure for a clean paired A/B

The repo's own instruction (`docs/OBJECTIVE.txt:95-96`): *"Nexus state is shared and mutable
across runs. Give each arm its own `history_scope_salt`, and make both arms equally warm or
equally cold."* The reference A/B that produced the doc-184/185 numbers used **two separate
strategy docs**, isolated salts, and `historical_lookback_enabled=False` on both
(`docs/handoffs/2026-08-03-full-session.md:98-101`,
`backend/tests/test_regime_conditional_core.py:5-12`).

Minimal procedure, in dependency order:

1. **Two docs, not one doc patched twice.** Copy doc-193 to a second Strategies doc (the
   `apply_doc193_*.py` scripts read/write `strategies[0].config`, the operative block —
   see the warning at `scripts/apply_doc193_backtest_sell_proceeds_credit.py:4-7`). Sequential
   patch/run/patch on one doc is only safe if nothing else can queue a run in between; two docs
   removes that race entirely and is what the last credible A/B did.
2. **Distinct `history_scope_salt` per arm** (e.g. `ab-2026-08-08-A` / `…-B`) in
   `strategies[0].config`. This changes `history_scope_id`, hence `runtime_instance_id`
   (`broker.py:6168`), hence every per-instance Nexus table, the lookback resume set, the
   snapshot-reuse hash and the sentiment cache.
3. **Equal warmth.** Either `historical_lookback_enabled=False` on both (doc-193's on-disk
   backup already has `historical_lookback_enabled: false`, `lookback_learning_days: 120`), or
   let both arms build the full lookback from a cold salt. Never one warm and one cold.
4. **Same everything else**: instance `v2-let-run-core`, granularity 3600, cash $6,000, same
   window, and — because a code change between runs is not noise — the same build
   (`broker.py:63-108` `_code_version_stamp`; the handoff measured a 6.12pp median spread across
   a commit boundary vs 2.11pp within one).
5. **Queue both runs with the same POST shape** (`run_validation_backtest.py`, or the local
   harness `scripts/local_backtest.py run_one`). Neither supports the salt — the salt is a
   property of the doc the instance points at, so arm selection is "which doc is the instance
   bound to", i.e. two instances or a doc swap between runs.
6. If a run is aborted or re-run, wipe its scoped state first:
   `scripts/clear_backtest_state.py --backtest-id <id> --apply` resolves the scoped
   `runtime_instance_id` and clears the 14 per-instance tables plus `NexusStrategyCache`.

---

## 3. WINDOWS — what the two files actually say

### `scripts/local_backtest_windows.json`

| name | instance | start | end | declared regime | notes in file |
|---|---|---|---|---|---|
| bull_known_2026-03-30 | alpaca-main | 2026-03-30 | 2026-04-27 | bull | calibrated +6.60% (bt 148462) |
| bear_known_2026-03-02 | alpaca-main | 2026-03-02 | 2026-03-30 | bear | calibrated +2.29% (bt 726941) |
| bull_oos_2026-01-05 | alpaca-main | 2026-01-05 | 2026-02-02 | bull | "OOS — confirm regime from log" |
| bull_oos_2025-10-06 | alpaca-main | 2025-10-06 | 2025-11-03 | bull | "OOS — confirm regime from log" |
| bear_oos_2025-12-01 | alpaca-main | 2025-12-01 | 2025-12-29 | bear | "OOS — confirm regime from log" |
| bear_oos_2026-04-27 | alpaca-main | 2026-04-27 | 2026-05-25 | bear | "OOS — confirm regime from log" |

All are granularity 3600, cash 6000. The file's own `_comment` says the four "oos" regimes are
**best-guess labels to confirm from the harness log's V31 regime path on first run**.

### `scripts/nexus_evidence_windows.json`

instance `alpaca-main`, cash 6000, 5 windows:
`bear` 2026-03-02→03-30, `full` 2026-03-02→04-27, `rally` 2026-04-27→05-25,
`flip` 2026-01-05→03-02, `june` 2026-06-01→07-01.
`"matrix": null` — the manifest that the runner requires before the first backtest of the
matrix has **never been filled in**, so this file is not currently runnable as evidence.

### OOS relative to the reference window 2026-01-01 → 2026-03-01

Out-of-sample (no calendar overlap):

- `bull_known_2026-03-30` (03-30→04-27)
- `bear_known_2026-03-02` (03-02→03-30)
- `bull_oos_2025-10-06` (2025-10-06→11-03)
- `bear_oos_2025-12-01` (2025-12-01→12-29)
- `bear_oos_2026-04-27` (04-27→05-25)
- evidence-file `rally` (04-27→05-25), `full` (03-02→04-27), `june` (2026-06-01→07-01)

**Not OOS:**

- `bull_oos_2026-01-05` (2026-01-05→02-02) — entirely inside the reference window despite its
  name.
- evidence-file `flip` (2026-01-05→03-02) — ~97% overlap with the reference window.

### Non-semiconductor leadership

**Neither file records sector leadership. There is no leadership field, and no other file maps
these windows to a leader.** State that plainly rather than guessing.

The one adjacent measurement in the repo is
`docs/handoffs/2026-08-06-can-this-reach-2-3x.md:35-48`: over **2025-12-01 → 2026-06-30**,
SOXX +114.4% vs SPY +9.4%, and 11 of the top-16 movers were semis. Every candidate window above
except `bull_oos_2025-10-06` (2025-10-06→11-03) falls inside that semiconductor-led span. That
makes `bull_oos_2025-10-06` the *only* candidate that is even plausibly non-semi-led, and the
repo contains **no measurement of what led that window** — it would have to be measured (e.g.
sector-ETF returns over 2025-10-06→11-03) before claiming it satisfies the OBJECTIVE's
"at least 1 where leadership is not semiconductors" (`docs/OBJECTIVE.txt:92-93`).

---

## 4. LIVE-VS-BACKTEST DIVERGENCE in `backend/broker.py`

### 4a. The sell-proceeds credit we just enabled — the important finding

Chain of code, in execution order:

| line | code |
|---|---|
| `broker.py:14168-14174` | reads `backtest_credit_sell_proceeds_enabled` off the first strategy config into `_scp_bt` |
| `broker.py:14177` | `_scp_credit_on = (mode == MODE_LIVE) or _scp_bt` |
| `broker.py:14178-14186` | the kill-switch `live_credit_sell_proceeds_enabled` is read **only when `mode == MODE_LIVE`**; in backtest `_scp_enabled` stays hard-coded `True` (`:14149`) |
| `broker.py:15016-15025` | if `_scp_credit_on and _scp_sell_proceeds`: `_sizing_ceiling = buy_ceiling(_sizing_ceiling, _scp_sell_proceeds, enabled=_scp_enabled)` |
| `broker.py:15026-15027` | `available = max(0.0, _sizing_ceiling - reserved_total - _effective_floor)`; `cash_to_use = min(cash_per_trade, available)` |
| `broker.py:15625-15640` | after each submit-successful sell, book `qty × frac × price` into `_scp_sell_proceeds` |
| `nexus_broker_utils.py:222-262` | `buy_ceiling` = `cash + 0.95 × Σ(proceeds)`, haircut clamped to [0,1] |

**Then the two modes diverge at the submit boundary:**

- **LIVE (Alpaca equities)** goes through `_build_strategy_stock_intent`
  (`broker.py:15478-15513`), and the buy quantity is computed with **no second cash clamp**
  (`broker.py:8308-8311`):
  ```python
  quantity = (Decimal(str(cash_to_use)) / Decimal(str(price))).quantize(Decimal("0.00000001"))
  ```
  So live really does spend the credited ceiling.
- **BACKTEST** goes through `_submit_portfolio_signal` → `PortfolioEmulator.execute_signal`
  (`broker.py:15562-15571`, `broker.py:10736-10744`), which **re-clamps**
  (`portfolio_emulator.py:1414-1423`):
  ```python
  reserved_cash = sum(... self._execution_cash_reservations.values())
  amount_to_use = min(cash_per_trade, self.get_buying_power(reserved_cash))
  ```
  and `get_buying_power` is `max(0.0, self._cash - self._withheld_cash() - reserved)`
  (`portfolio_emulator.py:408-420`).

Under next-event execution the funding sell has **not filled** when the paired buy is sized:
pending orders are only applied at the *start of the next tick*
(`broker.py:12098-12118`, `portfolio_emulator.process_price_events`). So in backtest,
`self._cash` is still the pre-sell cash and `get_buying_power() ≤ _cash_now`
(`_cash_now = portfolio_emulator.get_cash()`, `broker.py:14978`).

> **FLAG (highest-value finding).** The credit lifts the *broker's* gate in both modes, but in
> backtest the emulator clamps the order back down to pre-sell buying power. In live there is no
> such clamp on the Alpaca equity path. Two consequences:
> 1. Live can place a materially **larger** order than the identical backtest bar would —
>    exactly the direction that makes a backtest number un-reproducible live, and it grows with
>    conviction (the rotation case the lever was written for).
> 2. Whatever part of the +6.02% → +15.04% is attributed to this lever needs the *log* to
>    confirm the fill notional actually grew, not just the "Sell-proceeds credit: sizing ceiling
>    $X → $Y" line. Per `docs/OBJECTIVE.txt` ("Read the run"), pull `[execution] FILL BUY …`
>    for the 2026-01-13 SNDK bar cited in
>    `scripts/apply_doc193_backtest_sell_proceeds_credit.py:17-25` and compare qty×price to the
>    lifted ceiling. If the fill is still ≈ pre-sell cash, the lever is (partly) inert in
>    backtest and the gain came from the other four levers.
>
> Two further asymmetries in the same block: the backtest path has **no kill switch**
> (`live_credit_sell_proceeds_enabled` is only read under `mode == MODE_LIVE`,
> `broker.py:14178`), and the emulator additionally models **T+1 settlement** — only 95% of a
> sale is spendable immediately, the rest is withheld for a day
> (`portfolio_emulator.py:112-121`, `422-436`) — while the live path has no equivalent
> modelling at all beyond Alpaca's own buying power.

### 4b. Order execution — live-only paths a backtest never exercises

| line | gate | consequence if it misbehaves live |
|---|---|---|
| `broker.py:13732-13746` | `maybe_rollover_orders_today` (live only, 15s bound) | stale order cache blocks re-buys |
| `broker.py:13754-…` | per-tick kill-switch poll from `Instances` (live only) | no backtest analogue |
| `broker.py:13887-13900` | `portfolio_emulator.refresh_cash()` from Alpaca before sizing (live only, 15s bound) | on timeout, sizing uses cached cash |
| `broker.py:13962-14027` | broker-calendar market-open check (live only) — **fails closed on timeout**, deferring every decision this tick | live can silently skip a whole bar; backtest never does |
| `broker.py:14029-14045` | pre-submit quote refresh (live only, 5s deadline) | live fills at a different price than the bar price the backtest used |
| `broker.py:14473-14494` | `run_strategy` under a 15s watchdog in live; **direct call** in backtest (`:14495-14501`) — a timed-out spec ABSTAINS live | live decisions can differ from backtest on the same inputs |
| `broker.py:14658-14680` | `run_post_decision_strategies` under a 120s watchdog live; direct call in backtest | same |
| `broker.py:15083-15163` | price-sanity reject (>20% off last close) — **live only**, sets `cash_to_use = 0.0` | live-only buy rejections |
| `broker.py:15211-15213` | per-symbol market-open gate (live only) | |
| `broker.py:15260-15276` | `ordered_today` restart-dedup (live only) | live can skip a legitimate second order |
| `broker.py:15434-15521` | Alpaca stock order gate; `_submission.decision.allowed` can BLOCK an order ("ORDER GATE BLOCKED") | a whole class of live refusals with no backtest twin |
| `broker.py:14398-14407` | `_core_tick_ok`: live uses `_tick_mode != "IDLE"`, backtest uses `_dc_bt_sim` + `_nexus_last_tick_mode` | core funding release fires on different ticks |
| `broker.py:14413-14421` | `_residual_sleeve_release` gets the live order service only in live; backtest passes `None` (`:15791-15794`) | two code paths for the same sleeve |
| `broker.py:3861`, `3889` | sleeve state persist/restore is live-only | |
| `broker.py:10455-10466` | session hours differ by mode: LIVE 01–17, BACKTEST 05–20 | different bar sets |

Parity that **is** in place and worth keeping: the 15% single-position cap now applies in both
modes (`broker.py:15040-15051`, default `BROKER_MAX_SINGLE_POSITION_PCT=0.15`); the monthly
turnover budget is evaluated once per tick on the shared path (`broker.py:14425-14431`); the
turnover ledger and min-hold clock are booked after the branches converge
(`broker.py:15574-15593`).

### 4c. Cash crediting

- Backtest: T+1 settlement with `SETTLED_SELL_PROCEEDS_FRACTION = 0.95` and
  `DEFAULT_SETTLEMENT_DELAY = timedelta(days=1)` (`portfolio_emulator.py:112-121`), withheld via
  `_withhold_sell_proceeds` (`:422-436`), and a confirmed buy fill is rejected outright if it
  exceeds buying power (`portfolio_emulator.py:1105-1110`).
- Live: cash comes from `refresh_cash()` against Alpaca (`broker.py:13887`), plus the optional
  `live_use_buying_power_for_sizing` margin path — **live only**, `broker.py:14994-15007`:
  `_sizing_ceiling = max(_cash_now, _bp_cached)`.
- The legacy adapter path (`broker_adapters/alpaca.py:2622-2627`) does clamp
  `amount = min(cash_per_trade, self._cash)`, but the equity path in production does **not** go
  through it — it goes through `LiveOrderService.enqueue` (`broker.py:15506-15513`).

### 4d. Config the live run silently changes under you — **second-highest-value finding**

`broker.py:10354-10360` (live only):

```python
if mode == MODE_LIVE:
    for _spec in _cached_strategies:
        _spec_cfg = _spec.get("config") or {}
        _spec["config"] = _apply_live_overrides(_spec_cfg)
```

`backend/live_mode_overrides.py:21-51` merges `LIVE_OVERRIDES` **on top of** the doc's config,
and only three keys are user-overridable (`live_mode_overrides.py:74-80`:
`portfolio_drawdown_halt_pct`, `quality_filter_missing_metadata_policy`,
`break_glass_fresh_shield_enabled`). Against the most recent on-disk doc-193 backup
(`scripts/doc193_backup_patch_20260808T011456Z.json`, `strategies[0].config`):

| key | doc-193 (backtest) | live value | verdict |
|---|---|---|---|
| `portfolio_drawdown_halt_enabled` | `false` | **`true`** (not user-overridable) | **flips live** — a drawdown halt the backtest never modelled |
| `private_entity_bridge_enabled` | absent → strategy default **`True`** (`graph_nexus_analysis.py:25841`) | **`False`** | **flips live** — PE bridge runs in backtest, not live |
| `max_positions_breach_auto_rotate` | absent → strategy default **`True`** (`graph_nexus_analysis.py:28729`) | **`False`** | **flips live** — breach auto-heal runs in backtest, not live |
| `portfolio_drawdown_halt_pct` | `8` | `8` (user value survives) | ok |
| `quality_filter_missing_metadata_policy` | `"warn"` | `"warn"` (user-overridable) | ok |
| `break_glass_fresh_shield_enabled` | `false` | `false` (user-overridable) | ok |
| `analyst_panel_enabled` | `false` | `false` | ok |
| `nexus_live_max_llm_calls_per_cycle` / `nexus_live_llm_timeout_sec` / `nexus_live_graph_engine_ttl_sec` / `nexus_live_fail_closed_on_missing_mcap` / `v32_convert_cooldown_window_utc` | absent | set by overrides | **no consumer found** in `backend/` outside `broker.py:6745` (lookback prepass) — inert |

So the same doc, run live, is not the same strategy: three behavioural flags flip. Any claim
that a +15.04% backtest "will run live" has to account for those three, or the overrides list
has to be reconciled with doc-193 before launch.

---

## 5. PROMOTION CHECKLIST — what the actual gate is

### `scripts/validate_live_launch_readiness.py` is NOT the gate

It runs three RethinkDB hygiene checks (`:128-132`) and prints GREEN/YELLOW/RED (`:150-157`,
exit 0/1/2):

1. `_check_snapshot_present` (`:38-60`) — a backtest-origin `NexusStrategyCache` row exists for
   the instance; RED if none, YELLOW if its `end_date` is >7 days old.
2. `_check_no_legacy_live_strategy_cache` (`:63-74`) — YELLOW if live-origin rows remain.
3. `_check_per_instance_residue` (`:77-111`) — YELLOW if the 8 per-instance Graph Nexus tables
   still hold rows.

It defaults to `--instance main` (`:162`), not `alpaca-main`, and it checks **state hygiene
only** — nothing about P&L, PIT provenance or evidence. Its own note (`:124-127`) says the WAL
check was removed as a false GREEN.

`docs/runbooks/live-launch-checklist.md` wraps it in an operator procedure (T-24h lock config +
`audit_point_in_time_coverage` + backtest with `end_date=today` + snapshot-line check; T-1h
reconcile Alpaca, wait for settlement, `clear_main_instance_lookback_state.py` dry-run then
`--apply`, then this validator; T-15m start and tail the log). `:89-96` explicitly says it does
**not** protect against strategy bugs or profitability.

### The real, code-enforced gate on starting funded alpaca-main

`backend/instance.py:569-586`, immediately before `subprocess.Popen`:

```python
_requires_live_gate = (_kind == "kalshi"
    or brokerage_requires_live_gate(_instance_doc, _brokerage_doc, environ=os.environ))
_funded_alpaca = (... brokerage_type == "alpaca" and _brokerage_doc.get("alpaca_paper") is False)
if _requires_live_gate:
    _assert_live_broker_start_allowed(instance_id, _instance_doc)
if _funded_alpaca:
    _assert_watchdog_preflight()
_assert_clean_room_initial_value(instance_id, _instance_doc)
```

1. **`_assert_live_broker_start_allowed`** (`instance.py:435-446`) parses
   `Instances.<id>.live_readiness_report` and calls `assert_live_start_allowed`
   (`backend/live_readiness.py:182-195`): every check must pass, state must be `LIVE_ELIGIBLE`
   or `LIVE_RUNNING`, and the report must be artifact-bound to
   `INTELLISTOCK_DEPLOYED_ARTIFACT_SHA256`. Same assertion again in `server.py:206-207`.
   **There is no writer for `live_readiness_report` anywhere in `backend/`** — grep finds only
   readers (`instance.py:440`, `server.py:207`, `scripts/verify_inactive_deployment.py:300`).
   So today the gate is unsatisfiable without hand-writing a fingerprinted report onto the
   Instances row.
2. **`_assert_watchdog_preflight`** (`instance.py:168-186`) — funded Alpaca requires
   `ALPHA_MARK_WATCHDOG_ENABLED=1` plus `ALPACA_WATCHDOG_KEY`, `ALPACA_WATCHDOG_SECRET`,
   `RETHINKDB_HOST`.
3. **`_assert_clean_room_initial_value`** (`instance.py:133-165`) — `Instances.alpaca-main.initial_value`
   must be set, or the spawn is refused (the docstring says alpaca-main "is still configured to
   hit it the moment it is started").

The readiness state itself comes from `evaluate_promotion`
(`backend/benchmark_alpha/promotion.py:215-313`), evaluated by
`backend/scripts/verify_alpha_readiness.py`. Selected hard requirements:

- `point_in_time_months >= 24` **and** `point_in_time_provenance_verified is True` (`:224-228`)
  — the research-mode runs can never satisfy this.
- `unseen_months >= 12`, `regime_count >= 3`, `purged_fold_count >= 1`, a preregistered sealed
  holdout evaluated exactly once (`:229-236`).
- `median_annual_active_pp >= 8`, `target_annual_active_pp >= 10`, `bootstrap_active_low > 0`,
  `information_ratio >= 0.75`, `deflated_sharpe_probability >= 0.95`,
  `max_drawdown_magnitude <= 0.15`, `0.8 <= beta <= 1.1`, `profit_factor_after_costs > 1`,
  `positive_unseen_quarter_fraction >= 0.60`, parameter stability, leave-one-winner-out,
  concentration analysis (`:242-261`).
- artifact/config/model/data hashes must equal the deployed/observed ones (`:263-279`).
- ops: CI, chaos, restart-state, secret migration, rollback rehearsal, zero plaintext
  credentials, zero unresolved HIGH/CRITICAL, watchdog healthy ≤60s (`:280-300`).
- **paper first**: `paper_artifact_hash == artifact_hash`, `paper_config_hash == config_hash`,
  and `paper_trading_days >= MIN_PAPER_TRADING_DAYS = 60` (`:301-313`, `:27`).

Any failing reason in `_RESEARCH_REASONS` (which includes `point_in_time_provenance`) forces
state `RESEARCH` (`promotion.py:315-320`), i.e. **not even paper-eligible**.
`scripts/nexus_evidence_windows.json:_comment` restates it: "these five windows are
DEVELOPMENT/REGRESSION evidence only -- production eligibility still requires the repository's
`evaluate_promotion` contract".

### Summary of the gate

```
research run (pit_mode=research)  ->  ReadinessState.RESEARCH        [where we are]
    + strict PIT manifests covering >= 24 months
    + OOS / regime / holdout / cost / stability evidence
                                  ->  PAPER_ELIGIBLE
    + 60 paper trading days on the identical artifact + config hash
                                  ->  CANARY_ELIGIBLE -> LIVE_ELIGIBLE
    + fingerprinted live_readiness_report on Instances.alpaca-main   [no writer exists]
    + ALPHA_MARK_WATCHDOG_ENABLED=1 + watchdog creds
    + Instances.alpaca-main.initial_value set
    + explicit activation                                 ->  LIVE_RUNNING
```

Promotion can advance only one state at a time and never beyond what the evidence proves
(`live_readiness.py:61-95`).

---

## What I did not find (stated explicitly rather than guessed)

- No `pit_mode` value `'pit'` — the strict value is `'strict'`.
- No config key, script, or API field that sets `point_in_time_manifest` /
  `point_in_time_store` / `point_in_time_session_close_resolver` on a backtest outside tests.
- No support for `history_scope_salt` in `scripts/run_validation_backtest.py`,
  `scripts/local_backtest.py`, or the `POST /backtests` body.
- No sector-leadership metadata in `scripts/local_backtest_windows.json` or
  `scripts/nexus_evidence_windows.json`.
- No writer for `Instances.<id>.live_readiness_report` anywhere in `backend/`.
- No consumer for `nexus_live_max_llm_calls_per_cycle`, `nexus_live_llm_timeout_sec`,
  `nexus_live_graph_engine_ttl_sec`, `nexus_live_fail_closed_on_missing_mcap`, or
  `v32_convert_cooldown_window_utc` outside `live_mode_overrides.py` (and `broker.py:6745` for
  the first).
- `scripts/nexus_evidence_windows.json` has `"matrix": null`, so its own runner would refuse to
  start.
