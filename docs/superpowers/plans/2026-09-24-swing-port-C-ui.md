# Swing-trader port, plan C: web and mobile UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the operator the screens the swing and wheel lanes need. Link the Conviction LLM in the strategy editor. Approve or reject AI-scored signals on web and iOS. See the wheel's open puts. Read option positions and fills correctly in Live Trading.

**Architecture:** Web logic lives in two pure modules, `frontend/src/utils/swing.js` and `frontend/src/utils/optionPositions.js`. Node's built-in test runner covers them. Thin Vue SFCs render that logic and call plan B's routes with the same `fetch` + `authHeaders()` pattern `LearningView.vue` and `LiveReadinessCard.vue` use. Mobile adds a `features/swing` slice: a repository over `ApiClient`, a Riverpod family notifier that polls and owns decisions, and two widgets. The option display rules live on the `Position` and `Trade` models and a pure `core/models/option_symbol.dart`.

**Tech Stack:** Vue 3.5 `<script setup>`, Vite 8, Tailwind 3, Node 26 `node --test` (no new npm dependency). Flutter with Dart SDK ^3.12, flutter_riverpod 2.6 (no codegen), dio 5, flutter_test.

**Spec:** `docs/superpowers/specs/2026-09-24-swing-trader-port-design.md`. This plan covers the first bullet of §8 (`strategyConfig.js`), the Web and Mobile parts of §10, and the display of the option position fields §6.1 adds to `live_broker_fetch`.
**Shared contract:** `docs/superpowers/plans/2026-09-24-swing-port-interfaces.md`, §5 (SwingSignals) and §8 (ids, routes).

## Global Constraints

- Work on `research/swing-trader-port`. Nothing merges to `main` (spec §12).
- **No backend changes in this plan.** Plan B builds the routes; this plan consumes them. Plan A-live adds the position fields; this plan reads them.
- Use contract names exactly:
  - routes `GET /instances/{instance_id}/swing/signals?status=`, `POST /instances/{instance_id}/swing/signals/{signal_id}/decision` with body `{"decision", "reason"}`, `GET /instances/{instance_id}/wheel`;
  - decisions `approve | approve_half | reject`;
  - strategy ids `strategy_swing`, `strategy_wheel`; model key `conviction_llm_model_id`;
  - SwingSignals fields `id, lane, symbol, session, created_at, score, recommendation, reasoning, key_risks, size_adjustment, proposal, status`;
  - position fields `asset_class, side, multiplier, underlying, strike, expiry`.
- Web conventions: `<script setup>`, a per-component `authHeaders()` with `fetch`, the API base passed in as an `apiBase` prop (as `LiveReadinessCard` takes it), `glass-card` cards, and Tailwind classes copied from `LearningView.vue` / `LiveReadinessCard.vue`. No new npm dependency.
- Frontend unit tests use Node's built-in runner: `npm test` runs `node --test "tests/*.test.mjs"`. The glob form needs Node 21 or later; local is v26, and the Docker build runs only `npm run build`. SFCs have no runner. Their gate is `npm run build` plus the manual check written in the task.
- Mobile conventions:
  - Riverpod 2 providers written by hand.
  - `ApiClient` via `apiClientProvider`; failures arrive as `ApiError(message, statusCode:)`. A 401 already clears the session in `AuthInterceptor`.
  - Polling uses `IntervalPoller` plus `appLifecycleProvider`.
  - Confirmations use `showConfirmDialog` **without** `onConfirm`. That callback swallows exceptions, and the operator must see why a decision failed.
- Mobile gates: `flutter analyze` stays at the 23-info baseline, with 0 errors and 0 warnings. `flutter test` has 393 passing, plus exactly 2 pre-existing golden failures (`sector_3d_chart_golden_test.dart: drilled sector chart` and `strategy_trends_card_golden_test.dart`).
- A full `flutter test` run rewrites tracked PNGs in `mobile/test/features/dashboard/failures/` and creates untracked `sector_3d_drilled_*.png` there. Never stage them. Restore them with the command in "Final verification". Run per-task tests by path; they do not touch those files.
- Commits:
  - stage explicit paths only; **never stage `AGENTS.md` or `CLAUDE.md`**;
  - commit bodies contain no backticks;
  - the footer is exactly the two lines shown in each task.
- GitNexus (CLAUDE.md):
  - At the start of each task, run `mcp__gitnexus__impact({target, direction: "upstream"})` on every existing symbol the task modifies. If it warns the index is stale, run `npx gitnexus analyze` first.
  - On 2026-09-24 the index was 3 commits behind and returned `risk: UNKNOWN`, lower-bound, with no callers resolved, for Vue and Dart symbols. Confirm callers with the grep given in each task, and report the blast radius.
  - Warn the user and stop on HIGH or CRITICAL.
  - Run `mcp__gitnexus__detect_changes()` before every commit.
- Money semantics (spec §6.1):
  - A position's `market_value` and `unrealized_pnl` are broker dollars with the contract multiplier already included. **Never multiply them.**
  - `last_price` and `avg_entry_price` are per share, which for an option means per-share premium.
  - Only client-side `qty × price` totals get the ×100.

## Contract addendum (consumed here, not pinned by the interfaces doc)

The interfaces doc pins the SwingSignals document and the route paths. It does not pin the list envelope, the decision error codes, or the wheel payload. This plan consumes the shapes below. **Before Task 2**, open the interfaces doc and any plan B file:

- If plan B pins a different shape, change only these readers and their fixtures: `normalizeSignalList` and `parseWheelPayload` (web), `SwingRepository.pendingSignals` and `WheelSnapshot.fromJson` (mobile).
- If nothing pins these shapes, append this section to the interfaces doc as "## 9. UI-consumed shapes (plan C)". Stage that file with Task 2 **only if git already tracks it**. On 2026-09-24 it was untracked, and its owner is the orchestrator.

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

## Review Focus

1. **Approve double-tap.** Two clicks or taps land inside one frame, before the button re-renders disabled. Exactly one POST should go out, and the second click should do nothing. Pinned by Task 2 `createInFlightGuard refuses a second acquire until release (double click)` and Task 5 `a double tap sends one request`.
2. **Approving a signal another device already decided (400, or 404/409).** Show the server's reason, remove the card, and never let a racing poll bring back a card this device decided. Pinned by:
   - Task 2 `classifyDecisionFailure: another device decided first ...` and `withoutDecided drops ids ...`;
   - Task 5 `decided elsewhere (400) ...` and `a poll that raced a recorded decision ...`;
   - Task 6 `decided on another device (400) ...`.
3. **Session expires or permission is revoked mid-session (401/403).**
   - 401 on web stops polling and says "Session expired". On mobile the interceptor signs out, and the card stays.
   - 403 keeps the card with its buttons re-enabled and shows the reason.

   Pinned by Task 2 `classifyDecisionFailure: 401 stops polling, 403 keeps the card ...`, Task 5 `forbidden (403) ...` and `expired session (401) ...`, and Task 6 `forbidden (403): card stays and can be retried`.
4. **Very long LLM reasoning** (thousands of characters, unbroken tokens). It is collapsed by default behind "Show more", with no horizontal overflow on a 320pt phone. Pinned by Task 2 `reasoningPreview ...` and Task 6 `long reasoning on a 320pt phone: no overflow, collapsible`.
5. **An option position with no broker quote** (`current_price`, `market_value` and `unrealized_pl` all null). It shows dashes, never `$0.00`, never crashes, and offers no Close. Pinned by:
   - Task 4 `a short put with no quote yields no numbers to invent`. The web view's existing `fmtMoney`/`fmtPct` already print `—` for null; the task keeps it that way.
   - Task 7 `a short put with no quote keeps its nulls` and `short put with no quote: contracts, badges, dashes, no Close`.
   - Task 6 `lists open puts; a missing mark shows a dash`.

---

## File map

| File | Task | Responsibility |
|---|---|---|
| `frontend/package.json` | 1 | adds the `test` script |
| `frontend/tests/strategyConfig.test.mjs` | 1 | pins the swing and wheel field metadata and the conviction model card |
| `frontend/src/utils/strategyConfig.js` | 1 | `STRATEGY_FIELD_META` for both strategies, the `conviction_` role label and prefix |
| `frontend/src/utils/swing.js` | 2 | pure approval and wheel rules: lanes, parsing, formatting, error classes, double-click latch |
| `frontend/tests/swing.test.mjs` | 2 | tests for the above |
| `frontend/src/components/swing/PendingSignalsCard.vue` | 3 | pending signal cards: approve, approve ½, reject, confirm step, 30 s poll |
| `frontend/src/components/swing/WheelPanel.vue` | 3 | open puts, collateral, ITM, DTE, recent scans, 60 s poll |
| `frontend/src/views/InstanceDetailView.vue` | 3 | shows both cards when the strategy doc has a lane |
| `frontend/src/utils/optionPositions.js` | 4 | OCC parse, option detection, contracts label, ×100 totals, Close rule |
| `frontend/tests/optionPositions.test.mjs` | 4 | tests for the above |
| `frontend/src/views/LiveTradingView.vue` | 4 | option badge, Contracts, ×100 fill totals, Close hidden, no OCC history fetch |
| `mobile/lib/features/swing/data/swing_repository.dart` | 5 | models and the HTTP calls |
| `mobile/lib/features/swing/application/swing_controller.dart` | 5 | `swingLanesOf`, the polled `PendingSignalsNotifier` with `decide`, `wheelSnapshotProvider`, the copy |
| `mobile/test/features/swing/swing_fakes.dart` | 5 | `FakeSwingRepo` and fixtures, shared by the swing tests |
| `mobile/test/features/swing/swing_repository_test.dart` | 5 | parsing and request paths |
| `mobile/test/features/swing/swing_controller_test.dart` | 5 | decision rules |
| `mobile/lib/features/swing/presentation/pending_signals_section.dart` | 6 | the signal cards and the confirm flow |
| `mobile/lib/features/swing/presentation/wheel_card.dart` | 6 | the wheel card |
| `mobile/lib/features/instances/presentation/instance_detail_screen.dart` | 6 | places both widgets above `_StocksCard`; pull-to-refresh invalidates them |
| `mobile/test/features/swing/pending_signals_section_test.dart` | 6 | widget tests |
| `mobile/lib/core/models/option_symbol.dart` | 7 | pure OCC parse and description |
| `mobile/lib/features/live_trading/data/models/live_state.dart` | 7 | `Position` and `Trade` option fields and rules |
| `mobile/lib/features/live_trading/presentation/position_card.dart` | 7 | contracts, badges, dashes, no Close for options |
| `mobile/lib/features/live_trading/application/live_state_notifier.dart` | 7 | no historicals request for OCC symbols |
| `mobile/lib/features/live_trading/presentation/live_trading_screen.dart` | 7 | `_TradeRow`: contracts, ×100 total, option badge |
| `mobile/lib/features/dashboard/application/portfolio_analytics.dart` | 7 | excludes options from the sector breakdown |
| `mobile/lib/features/dashboard/application/insights_controller.dart` | 7 | skips the sector lookup for options |
| `mobile/test/core/models/option_symbol_test.dart`, `mobile/test/features/live_trading/live_state_options_test.dart`, `mobile/test/features/live_trading/position_card_test.dart`, `mobile/test/features/dashboard/portfolio_analytics_test.dart` | 7 | tests |

Task order: 1 → 2 → 3 on web and 5 → 6 on mobile. Tasks 4 and 7 are independent of the rest; Task 4 only needs Task 1's `npm test` script.

## Pre-flight

- [ ] Record the baselines. They must match before Task 1.

```bash
cd frontend && npm run build 2>&1 | grep "built in"        # ✓ built in ...
cd ../mobile && flutter analyze 2>&1 | tail -1               # 23 issues found. (all "info")
flutter test test/features/dashboard/portfolio_analytics_test.dart 2>&1 | tail -1   # All tests passed!
```

---

### Task 1: Swing and wheel labels and the Conviction LLM card (`strategyConfig.js`)

**Files:**
- Modify: `frontend/package.json` (the `scripts` block)
- Modify: `frontend/src/utils/strategyConfig.js:299-301` (end of `STRATEGY_FIELD_META`), `:365-373` (`KNOWN_LLM_ROLE_LABELS`), `:384-386` (`KNOWN_LLM_ROLE_PREFIXES_BY_STRATEGY`)
- Test: `frontend/tests/strategyConfig.test.mjs`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `npm test` in `frontend/`, which runs `node --test "tests/*.test.mjs"`. Tasks 2 and 4 add files to `frontend/tests/`.
  - `STRATEGY_FIELD_META.strategy_swing`, `STRATEGY_FIELD_META.strategy_wheel`, `KNOWN_LLM_ROLE_LABELS['conviction_'] = 'Conviction LLM'`, and `KNOWN_LLM_ROLE_PREFIXES_BY_STRATEGY.strategy_swing = .strategy_wheel = ['conviction_']`. No exported signature changes.

The conviction model card needs no new code path, which is confirmed below:
- `getStrategyLlmConfigGroups` (468-498) turns any key ending in `llm_model_id` into a group.
- `isLlmManagedConfigField` (507) hides that group's keys from the plain field list.
- `applyStrategyLlmDraft` (654-670) stores `conviction_llm_model_id` plus a provider and model summary.

Registering the prefix makes the card appear even on a document saved before the header gained the key.

- [ ] **Step 0: Impact.** Run `mcp__gitnexus__impact` upstream on `getStrategyConfigFieldMeta`, `getStrategyLlmConfigGroups`, `isLlmManagedConfigField` and `applyStrategyLlmDraft`. Their bodies are unchanged; the constants they read gain keys. Confirm callers:

```bash
grep -rn -E "getStrategyConfigFieldMeta|getStrategyLlmConfigGroups|isLlmManagedConfigField|applyStrategyLlmDraft" frontend/src --include=*.vue
```
Expected: only `views/InstancesView.vue` and `views/InstanceDetailView.vue`. The change is additive, so the risk is LOW. Existing `graph_nexus_analysis` output is pinned by the last test below.

- [ ] **Step 1: Add the test script.** In `frontend/package.json` replace

```json
    "build": "vite build",
    "preview": "vite preview"
```
with
```json
    "build": "vite build",
    "preview": "vite preview",
    "test": "node --test \"tests/*.test.mjs\""
```

- [ ] **Step 2: Write the failing test** `frontend/tests/strategyConfig.test.mjs`:

```js
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  applyStrategyLlmDraft,
  getStrategyConfigFieldMeta,
  getStrategyLlmConfigGroups,
  isLlmManagedConfigField,
} from '../src/utils/strategyConfig.js'

// Every header key from spec section 5.1 / 5.2 that renders as a plain field.
// conviction_llm_model_id is deliberately absent: it renders as the model card.
const SWING_KEYS = [
  'strategy_swing_enabled', 'rsi_period', 'rsi_entry_max', 'rsi_overbought',
  'sma_long', 'macd_fast', 'macd_slow', 'macd_signal', 'vol_avg_period',
  'adx_period', 'adx_min', 'spy_buffer', 'vix_max', 'position_size_pct',
  'max_positions', 'max_per_sector', 'profit_target', 'stop_loss',
  'bear_regime_days', 'defensive_universe', 'earnings_hard_block_days',
  'ai_gate_enabled', 'ai_approve_threshold', 'ai_review_threshold', 'scan_time_et',
  'live_max_order_fraction', 'live_max_symbol_fraction', 'live_max_leveraged_fraction',
  'live_soft_drawdown', 'live_hard_drawdown', 'live_kill_drawdown',
  'honour_single_position_cap', 'broker_max_single_position_pct',
]
const WHEEL_KEYS = [
  'strategy_wheel_enabled', 'rsi_min', 'rsi_max', 'sma_trend', 'atr_period',
  'strike_atr_mult', 'min_premium_pct', 'target_delta', 'days_to_expiry',
  'max_collateral_pct', 'max_per_sector', 'auto_covered_call',
  'approve_threshold', 'review_threshold', 'earnings_block_days',
  'limit_bid_mult', 'scan_weekday', 'scan_time_et', 'monitor_time_et',
]
const CONVICTION_KEYS = [
  'conviction_llm_model_id', 'conviction_llm_provider',
  'conviction_llm_model', 'conviction_llm_api_key',
]

for (const [strategy, keys] of [['strategy_swing', SWING_KEYS], ['strategy_wheel', WHEEL_KEYS]]) {
  test(`${strategy}: every header key has a label and a one-line description`, () => {
    for (const key of keys) {
      const meta = getStrategyConfigFieldMeta(strategy, key)
      assert.ok(meta.label.trim(), `${key}: empty label`)
      assert.ok(meta.description.trim(), `${key}: no description`)
      assert.ok(!meta.description.includes('\n'), `${key}: description spans lines`)
    }
  })

  test(`${strategy}: the conviction model is a model card even before its key exists`, () => {
    const groups = getStrategyLlmConfigGroups(strategy, {}, {})
    assert.deepEqual(groups.map(g => g.prefix), ['conviction_'])
    assert.equal(groups[0].label, 'Conviction LLM')
    assert.equal(groups[0].modelIdKey, 'conviction_llm_model_id')
    assert.equal(groups[0].providerKey, 'conviction_llm_provider')
  })

  test(`${strategy}: conviction keys stay out of the plain field list`, () => {
    const cfg = { conviction_llm_model_id: '', rsi_period: 14 }
    for (const key of CONVICTION_KEYS) {
      assert.equal(isLlmManagedConfigField(strategy, key, cfg, cfg), true, key)
    }
    assert.equal(isLlmManagedConfigField(strategy, 'rsi_period', cfg, cfg), false)
  })
}

test('the two lanes describe scan_time_et differently', () => {
  assert.notEqual(
    getStrategyConfigFieldMeta('strategy_swing', 'scan_time_et').description,
    getStrategyConfigFieldMeta('strategy_wheel', 'scan_time_et').description,
  )
})

test('linking a saved model stores the reference and drops inline credentials', () => {
  const [group] = getStrategyLlmConfigGroups('strategy_swing', {}, {})
  const next = applyStrategyLlmDraft(
    { rsi_period: 14, conviction_llm_api_key: 'sk-stale', conviction_llm_model: 'old' },
    group,
    { modelId: 'model-123', provider: 'Anthropic', model: 'claude-x', apiKey: 'never-stored' },
  )
  assert.deepEqual(next, {
    rsi_period: 14,
    conviction_llm_model_id: 'model-123',
    conviction_llm_provider: 'anthropic',
    conviction_llm_model: 'claude-x',
  })
})

test('graph_nexus_analysis role groups are unchanged', () => {
  const groups = getStrategyLlmConfigGroups('graph_nexus_analysis', {}, {})
  assert.deepEqual(groups.map(g => g.prefix), [
    '', 'analyst_panel_', 'company_article_', 'sentiment_',
    'event_maintenance_', 'macro_article_', 'overlay_',
  ])
})
```

- [ ] **Step 3: Run it and watch it fail.**

Run: `cd frontend && npm test`
Expected: `ℹ pass 3`, `ℹ fail 6`. The failures are both "every header key has a label" tests (`rsi_period: no description`), both "model card even before its key exists" tests (`[] !== ['conviction_']`), "the two lanes describe scan_time_et differently", and "linking a saved model" (`group` is undefined). The two "conviction keys stay out" tests and the nexus test pass already.

- [ ] **Step 4: Implement.** Make three edits to `frontend/src/utils/strategyConfig.js`.

4a. Add the two strategies to `STRATEGY_FIELD_META`. Replace the map's closing lines:

```js
    },
  },
}

export const LLM_PROVIDER_OPTIONS = [
```
with
```js
    },
  },
  // Spec 2026-09-24 section 5.1. Defaults are ST's (swing_trader/constants.py).
  strategy_swing: {
    strategy_swing_enabled: {
      label: 'Swing Lane Enabled',
      description: 'Master switch for the swing lane, in backtests and live. Off means the lane emits nothing.',
    },
    rsi_period: {
      label: 'RSI Period (Bars)',
      description: 'Daily bars in the RSI behind both the entry filter and the RSI-cross exit. ST default 14.',
    },
    rsi_entry_max: {
      label: 'Max RSI At Entry',
      description: 'An entry needs RSI at or below this value. ST default 50.',
    },
    rsi_overbought: {
      label: 'RSI Exit Level',
      description: 'The overbought level the RSI-cross exit watches. ST default 70.',
    },
    sma_long: {
      label: 'Long Trend SMA (Bars)',
      description: 'Length of the long-term trend filter in daily bars. ST default 200.',
    },
    macd_fast: {
      label: 'MACD Fast EMA (Bars)',
      description: 'Fast EMA length of the MACD entry filter. ST default 12.',
    },
    macd_slow: {
      label: 'MACD Slow EMA (Bars)',
      description: 'Slow EMA length of the MACD entry filter. ST default 26.',
    },
    macd_signal: {
      label: 'MACD Signal EMA (Bars)',
      description: 'Signal-line EMA length of the MACD entry filter. ST default 9.',
    },
    vol_avg_period: {
      label: 'Volume Average (Bars)',
      description: 'Bars in the volume average the entry filter compares against. ST default 20.',
    },
    adx_period: {
      label: 'ADX Period (Bars)',
      description: 'Lookback of the ADX trend-strength filter. ST default 14.',
    },
    adx_min: {
      label: 'Min ADX',
      description: 'An entry needs ADX at or above this trend-strength floor. ST default 15.',
    },
    spy_buffer: {
      label: 'SPY Regime Buffer',
      description: 'The bull regime needs SPY above its 200-day SMA times this multiplier. ST default 1.03.',
    },
    vix_max: {
      label: 'Max VIX',
      description: 'The bull regime also needs VIX at or below this level. ST default 25.',
    },
    position_size_pct: {
      label: 'Position Size (Fraction Of Equity)',
      description: 'Each entry buys this fraction of equity in whole shares, scaled by the AI size adjustment. ST default 0.125.',
    },
    max_positions: {
      label: 'Max Open Positions',
      description: 'The lane holds at most this many swing positions at once. ST default 8.',
    },
    max_per_sector: {
      label: 'Max Positions Per Sector',
      description: 'At most this many open swing positions may share one GICS sector. ST default 1.',
    },
    profit_target: {
      label: 'Take-Profit (Fraction)',
      description: 'The bracket take-profit sits this far above the prior close. ST default 0.09 (+9%).',
    },
    stop_loss: {
      label: 'Stop-Loss (Fraction)',
      description: 'The bracket stop sits this far below the prior close. ST default 0.06 (-6%).',
    },
    bear_regime_days: {
      label: 'Bear Sessions Before Defensive Mode',
      description: 'Consecutive bear-regime NY sessions before the lane trades the defensive universe. ST default 10.',
    },
    defensive_universe: {
      label: 'Defensive Universe',
      description: 'Symbols the lane may buy in bear mode. ST default XLP, XLU, XLV, GLD, SHY.',
    },
    earnings_hard_block_days: {
      label: 'Earnings Block (Days)',
      description: 'Live only: no entry within this many days of an earnings date. Off in backtests, as in ST. ST default 5.',
    },
    ai_gate_enabled: {
      label: 'AI Conviction Gate',
      description: 'Live only: the linked Conviction LLM scores every candidate before it is placed. With no model linked, live entries are refused.',
    },
    ai_approve_threshold: {
      label: 'AI Auto-Approve Score',
      description: 'Scores at or above this are placed without asking. ST default 75.',
    },
    ai_review_threshold: {
      label: 'AI Review Score',
      description: 'Scores from this up to the auto-approve score wait for your approval on web or iOS. Lower scores are rejected. ST default 50.',
    },
    scan_time_et: {
      label: 'Scan Time (ET)',
      description: 'The live scan runs once per NY session, at the first tick at or after this time, and submits GTC brackets that queue for the open. ST default 09:15.',
    },
    live_max_order_fraction: {
      label: 'Live Max Order Fraction',
      description: 'The order gate refuses any single order larger than this fraction of equity.',
    },
    live_max_symbol_fraction: {
      label: 'Live Max Symbol Fraction',
      description: 'The order gate refuses a buy that would take one symbol above this fraction of equity.',
    },
    live_max_leveraged_fraction: {
      label: 'Live Max Leveraged-ETF Fraction',
      description: 'The order gate caps leveraged-ETF exposure at this fraction of equity.',
    },
    live_soft_drawdown: {
      label: 'Live Soft Drawdown',
      description: 'Drawdown from the equity peak that puts the account at the soft risk level, where new buys freeze.',
    },
    live_hard_drawdown: {
      label: 'Live Hard Drawdown',
      description: 'Drawdown from the equity peak that puts the account at the hard risk level. Must sit above the soft rung.',
    },
    live_kill_drawdown: {
      label: 'Live Kill Drawdown',
      description: 'Drawdown from the equity peak that reaches the kill level: working orders are cancelled and trading halts. Must sit above the hard rung.',
    },
    honour_single_position_cap: {
      label: 'Honour Single-Position Cap',
      description: 'Apply the broker single-position cap below to every swing buy.',
    },
    broker_max_single_position_pct: {
      label: 'Single-Position Cap (Fraction)',
      description: 'No single position may exceed this fraction of equity.',
    },
  },
  // Spec 2026-09-24 section 5.2. Live and paper only; backtests skip the wheel.
  strategy_wheel: {
    strategy_wheel_enabled: {
      label: 'Wheel Lane Enabled',
      description: 'Master switch for the cash-secured-put wheel. Live and paper only; the account needs options level 1 or higher.',
    },
    rsi_min: {
      label: 'Min RSI',
      description: 'A put candidate needs RSI at or above this value. ST default 30.',
    },
    rsi_max: {
      label: 'Max RSI',
      description: 'A put candidate needs RSI at or below this value. ST default 60.',
    },
    sma_trend: {
      label: 'Trend SMA (Bars)',
      description: 'Length of the trend filter in daily bars. ST default 50.',
    },
    atr_period: {
      label: 'ATR Period (Bars)',
      description: 'Lookback of the ATR the candidate screen uses to place the strike. ST default 14.',
    },
    strike_atr_mult: {
      label: 'Strike Offset (ATR Multiple)',
      description: 'How many ATRs below the price the screen looks for a strike. ST default 0.5.',
    },
    min_premium_pct: {
      label: 'Min Premium (Fraction)',
      description: 'Skip a candidate whose premium is below this fraction of the price. ST default 0.005 (0.5%).',
    },
    target_delta: {
      label: 'Target Put Delta',
      description: 'The lane sells the put whose delta is closest to this, read from the full chain. ST default 0.25.',
    },
    days_to_expiry: {
      label: 'Days To Expiry',
      description: 'Target days to expiry for the sold put. ST default 7.',
    },
    max_collateral_pct: {
      label: 'Max Collateral Per Underlying',
      description: 'Cash-secured collateral on one underlying, counting puts already open, stays under this fraction of equity. ST default 0.25.',
    },
    max_per_sector: {
      label: 'Max Puts Per Sector',
      description: 'At most this many open puts may share one sector. ST default 2.',
    },
    auto_covered_call: {
      label: 'Auto Covered Call',
      description: 'After an assignment, sell a covered call automatically. Off logs the candidate only (dry run), as in ST.',
    },
    approve_threshold: {
      label: 'AI Auto-Approve Score',
      description: 'Put candidates scoring at or above this are sold without asking. ST default 75.',
    },
    review_threshold: {
      label: 'AI Review Score',
      description: 'Scores from this up to the auto-approve score wait for your approval on web or iOS. Lower scores are rejected. ST default 50.',
    },
    earnings_block_days: {
      label: 'Earnings Block (Days)',
      description: 'No put is sold within this many days of an earnings date. ST default 7.',
    },
    limit_bid_mult: {
      label: 'Limit Price (Bid Multiple)',
      description: 'The sell-to-open limit starts at the live bid times this, then follows the ST fallback ladder. ST default 0.95.',
    },
    scan_weekday: {
      label: 'Scan Weekday',
      description: '0 is Monday. If the Monday scan does not complete, it runs on Tuesday. ST default 0.',
    },
    scan_time_et: {
      label: 'Scan Time (ET)',
      description: 'The weekly put scan runs at the first tick at or after this time on the scan weekday. ST default 10:30.',
    },
    monitor_time_et: {
      label: 'Monitor Time (ET)',
      description: 'Daily check that buys a put back at 10% ITM, at 5% ITM with 2 or fewer days left, or ITM on expiry day. ST default 15:45.',
    },
  },
}

export const LLM_PROVIDER_OPTIONS = [
```

4b. Add the role label. Replace

```js
  'analyst_panel_': 'Analyst Panel LLM (R1+R2 Debate)',
}
```
with
```js
  'analyst_panel_': 'Analyst Panel LLM (R1+R2 Debate)',
  'conviction_': 'Conviction LLM',
}
```

4c. Register the prefix. Replace

```js
  graph_nexus_analysis: ['', 'sentiment_', 'company_article_', 'macro_article_', 'event_maintenance_', 'overlay_', 'analyst_panel_'],
}
```
with
```js
  graph_nexus_analysis: ['', 'sentiment_', 'company_article_', 'macro_article_', 'event_maintenance_', 'overlay_', 'analyst_panel_'],
  // Registered, not only discovered from a key: a document saved before the
  // header gained conviction_llm_model_id still shows the model card.
  strategy_swing: ['conviction_'],
  strategy_wheel: ['conviction_'],
}
```

- [ ] **Step 5: Run the tests and the build.**

Run: `cd frontend && npm test && npm run build`
Expected: `ℹ pass 9`, `ℹ fail 0`, then `✓ built in`.

- [ ] **Step 6: Manual check.** This needs plan A's `strategy_swing.py` on the backend so it appears in `/strategies/available`. If it is not there yet, note that and skip.
  1. Run `npm run dev`, open an equity instance, click Strategy → Edit, and add sub-strategy `strategy_swing`.
  2. Every field shows the label and description above.
  3. A "Conviction LLM" card is present and `conviction_llm_model_id` is not listed as a plain field.
  4. Link a saved model and save. Reopen: the card shows the saved model's name.

- [ ] **Step 7: Check scope and commit.** Run `mcp__gitnexus__detect_changes()`. Expected: only `strategyConfig.js` constants.

```bash
git add frontend/package.json frontend/tests/strategyConfig.test.mjs frontend/src/utils/strategyConfig.js
git commit -F - <<'EOF'
feat(web): label the swing and wheel fields and the Conviction LLM card

Adds a label and a one-line description for every strategy_swing and
strategy_wheel header key, and registers the conviction_ LLM role for both
strategies so the model card appears even on a document saved before the key
existed. Adds a zero-dependency node --test harness for the pure frontend
utilities (npm test).

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 2: Pure approval and wheel rules (`frontend/src/utils/swing.js`)

**Files:**
- Create: `frontend/src/utils/swing.js`
- Test: `frontend/tests/swing.test.mjs`

**Interfaces:**
- Consumes: the `npm test` script (Task 1); the contract addendum shapes.
- Produces (all named exports, used by Task 3):
  - `swingLanesOf(strategyDoc) -> {swing: boolean, wheel: boolean, any: boolean}`
  - `normalizeSignalList(payload) -> SwingSignal[]` (pending only, newest first)
  - `withoutDecided(signals, decidedIds: Set) -> SwingSignal[]`
  - `scoreTone(score) -> 'high'|'mid'|'low'|'unknown'`
  - `joinKeyRisks(keyRisks) -> string`
  - `REASONING_PREVIEW_CHARS = 280`; `reasoningPreview(text, limit?) -> {text, truncated}`
  - `DECISION_LABELS`; `decisionsFor(signal) -> string[]`
  - `fmtUsd(value) -> string`
  - `proposalRows(signal) -> {label, value, mono?}[]`
  - `confirmPrompt(signal, decision)`, `decisionSuccessMessage(signal, decision)`, `detailText(detail)`, each returning a string
  - `classifyDecisionFailure(status, detail) -> {kind: 'unauthorized'|'forbidden'|'stale'|'failed', removeCard, stopPolling, message}`
  - `createInFlightGuard() -> {tryAcquire(id), release(id), has(id)}`
  - `parseWheelPayload(payload) -> {openPuts, collateralTotal, cash, recentScans}` (camelCase rows)
  - `itmTone(itmPct, dte) -> 'alert'|'itm'|'otm'|'unknown'`; `fmtItm(itmPct) -> string`

- [ ] **Step 0: Contract check.** Run the check in "Contract addendum": open the interfaces doc and any plan B file. New file only, so there is no GitNexus impact to run.

- [ ] **Step 1: Write the failing test** `frontend/tests/swing.test.mjs`:

```js
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  DECISION_LABELS,
  REASONING_PREVIEW_CHARS,
  classifyDecisionFailure,
  confirmPrompt,
  createInFlightGuard,
  decisionSuccessMessage,
  decisionsFor,
  detailText,
  fmtItm,
  itmTone,
  joinKeyRisks,
  normalizeSignalList,
  parseWheelPayload,
  proposalRows,
  reasoningPreview,
  scoreTone,
  swingLanesOf,
  withoutDecided,
} from '../src/utils/swing.js'

const SWING = {
  id: 'a1', instance_id: 'swing-paper', lane: 'swing', symbol: 'AAPL',
  session: '2026-09-24', created_at: '2026-09-24T13:15:02Z', score: 62,
  recommendation: 'REVIEW', reasoning: 'Pullback to the 50-day in an uptrend.',
  key_risks: ['earnings in 9 days', '', '  sector rotation  '], size_adjustment: 1.0,
  proposal: { entry: 200.0, stop: 188.0, target: 218.0, shares: 6 }, status: 'pending',
}
const WHEEL = {
  id: 'w1', instance_id: 'swing-paper', lane: 'wheel', symbol: 'APH',
  session: '2026-09-21', created_at: '2026-09-21T14:30:00Z', score: 55,
  recommendation: 'REVIEW', reasoning: 'IV rank is high.', key_risks: [],
  size_adjustment: 1.0, status: 'pending',
  proposal: { contract: 'APH261002P00130000', strike: 130, expiry: '2026-10-02',
    qty: 1, limit_price: 1.23, premium_est: 1.3, delta: -0.24 },
}

test('swingLanesOf reads lowercase ids and CamelCase class names', () => {
  assert.deepEqual(swingLanesOf({ strategies: [{ strategy: 'strategy_swing' }] }),
    { swing: true, wheel: false, any: true })
  assert.deepEqual(swingLanesOf({ strategies: [{ strategy: 'StrategyWheel' }, { strategy: 'strategy_eb' }] }),
    { swing: false, wheel: true, any: true })
  assert.deepEqual(swingLanesOf({ strategies: [{ strategy: 'strategy_eb' }] }),
    { swing: false, wheel: false, any: false })
  assert.deepEqual(swingLanesOf(null), { swing: false, wheel: false, any: false })
  assert.deepEqual(swingLanesOf({ strategies: 'oops' }), { swing: false, wheel: false, any: false })
})

test('normalizeSignalList accepts a bare list or {signals}, keeps pending only, newest first', () => {
  const decided = { ...SWING, id: 'old', status: 'approved' }
  assert.deepEqual(normalizeSignalList([WHEEL, SWING, decided, null, { status: 'pending' }]).map(s => s.id), ['a1', 'w1'])
  assert.deepEqual(normalizeSignalList({ signals: [SWING] }).map(s => s.id), ['a1'])
  assert.deepEqual(normalizeSignalList({ detail: 'nope' }), [])
})

test('withoutDecided drops ids this page already decided, so a poll cannot resurrect a card', () => {
  assert.deepEqual(withoutDecided([SWING, WHEEL], new Set(['a1'])).map(s => s.id), ['w1'])
})

test('scoreTone bands', () => {
  assert.equal(scoreTone(80), 'high')
  assert.equal(scoreTone(75), 'high')
  assert.equal(scoreTone(74), 'mid')
  assert.equal(scoreTone(50), 'mid')
  assert.equal(scoreTone(49), 'low')
  assert.equal(scoreTone(null), 'unknown')
  assert.equal(scoreTone('x'), 'unknown')
})

test('joinKeyRisks trims, drops blanks and joins with a middle dot', () => {
  assert.equal(joinKeyRisks(SWING.key_risks), 'earnings in 9 days · sector rotation')
  assert.equal(joinKeyRisks(undefined), '')
})

test('reasoningPreview leaves short text alone and cuts long text on a word', () => {
  assert.deepEqual(reasoningPreview('short'), { text: 'short', truncated: false })
  const long = 'word '.repeat(400)
  const out = reasoningPreview(long)
  assert.equal(out.truncated, true)
  assert.ok(out.text.length <= REASONING_PREVIEW_CHARS + 1)
  assert.ok(out.text.endsWith('…'))
  const unbroken = 'x'.repeat(5000)
  assert.equal(reasoningPreview(unbroken).text.length, REASONING_PREVIEW_CHARS + 1)
})

test('decisionsFor offers Approve half to swing only', () => {
  assert.deepEqual(decisionsFor(SWING), ['approve', 'approve_half', 'reject'])
  assert.deepEqual(decisionsFor(WHEEL), ['approve', 'reject'])
  assert.deepEqual(decisionsFor({}), ['approve', 'reject'])
  assert.equal(DECISION_LABELS.approve_half, 'Approve ½')
})

test('proposalRows: swing shows entry, stop, target, shares', () => {
  assert.deepEqual(proposalRows(SWING).map(r => [r.label, r.value]), [
    ['Entry', '$200.00'], ['Stop', '$188.00'], ['Target', '$218.00'], ['Shares', '6'],
  ])
})

test('proposalRows: wheel shows contract, strike, expiry, qty, limit, premium and collateral', () => {
  assert.deepEqual(proposalRows(WHEEL).map(r => [r.label, r.value]), [
    ['Contract', 'APH261002P00130000'], ['Strike', '$130.00'], ['Expiry', '2026-10-02'],
    ['Qty', '1'], ['Limit', '$1.23'], ['Premium', '$1.30 ($130.00)'], ['Collateral', '$13,000.00'],
  ])
})

test('proposalRows tolerates a missing proposal', () => {
  assert.deepEqual(proposalRows({ lane: 'swing' }).map(r => r.value), ['—', '—', '—', '—'])
})

test('confirmPrompt and decisionSuccessMessage name the symbol and the decision', () => {
  assert.match(confirmPrompt(SWING, 'approve'), /AAPL/)
  assert.match(confirmPrompt(SWING, 'approve_half'), /half/)
  assert.match(confirmPrompt(WHEEL, 'reject'), /final/)
  assert.match(decisionSuccessMessage(SWING, 'approve'), /^Approved AAPL/)
  assert.match(decisionSuccessMessage(WHEEL, 'reject'), /^Rejected APH/)
})

test('detailText flattens FastAPI detail shapes', () => {
  assert.equal(detailText('signal is not pending'), 'signal is not pending')
  assert.equal(detailText([{ msg: 'bad decision' }, { message: 'x' }]), 'bad decision; x')
  assert.equal(detailText(null), '')
})

test('classifyDecisionFailure: another device decided first -> remove card, show why', () => {
  for (const status of [400, 404, 409]) {
    const v = classifyDecisionFailure(status, 'signal a1 is approved, not pending')
    assert.equal(v.kind, 'stale')
    assert.equal(v.removeCard, true)
    assert.equal(v.stopPolling, false)
    assert.equal(v.message, 'signal a1 is approved, not pending')
  }
  assert.match(classifyDecisionFailure(400, '').message, /no longer pending/)
})

test('classifyDecisionFailure: 401 stops polling, 403 keeps the card, 5xx keeps the card', () => {
  assert.deepEqual(
    [classifyDecisionFailure(401, '').kind, classifyDecisionFailure(401, '').stopPolling, classifyDecisionFailure(401, '').removeCard],
    ['unauthorized', true, false])
  const forbidden = classifyDecisionFailure(403, 'not your instance')
  assert.deepEqual([forbidden.kind, forbidden.removeCard, forbidden.message], ['forbidden', false, 'not your instance'])
  const boom = classifyDecisionFailure(502, '')
  assert.deepEqual([boom.kind, boom.removeCard, boom.message], ['failed', false, 'Request failed (502)'])
  assert.equal(classifyDecisionFailure(0, '').message, 'Could not reach the server.')
})

test('createInFlightGuard refuses a second acquire until release (double click)', () => {
  const guard = createInFlightGuard()
  assert.equal(guard.tryAcquire('a1'), true)
  assert.equal(guard.tryAcquire('a1'), false)
  assert.equal(guard.has('a1'), true)
  assert.equal(guard.tryAcquire('w1'), true)
  guard.release('a1')
  assert.equal(guard.tryAcquire('a1'), true)
})

test('parseWheelPayload maps the addendum shape and tolerates junk', () => {
  const w = parseWheelPayload({
    open_puts: [{ contract: 'APH261002P00130000', underlying: 'APH', strike: 130, expiry: '2026-10-02',
      qty: 1, avg_entry_price: 1.23, current_price: null, underlying_price: 127.4,
      itm_pct: 2.0, dte: 8, collateral: 13000, unrealized_pl: null }, 'junk'],
    collateral_total: 13000, cash: 25000,
    recent_scans: [{ id: 's1', session: '2026-09-21', created_at: '2026-09-21T14:30:00Z', symbol: 'APH',
      stock_price: 134.2, strike: 130, expiry: '2026-10-02', premium_est: 1.3, score: 55,
      recommendation: 'REVIEW', status: 'pending', skip_reason: null }],
  })
  assert.equal(w.openPuts.length, 1)
  assert.equal(w.openPuts[0].currentPrice, null)
  assert.equal(w.openPuts[0].itmPct, 2)
  assert.equal(w.collateralTotal, 13000)
  assert.equal(w.recentScans[0].skipReason, '')
  assert.deepEqual(parseWheelPayload(null), { openPuts: [], collateralTotal: null, cash: null, recentScans: [] })
})

test('itmTone mirrors the 15:45 monitor buy-back rules', () => {
  assert.equal(itmTone(10, 20), 'alert')
  assert.equal(itmTone(5, 2), 'alert')
  assert.equal(itmTone(0.4, 0), 'alert')
  assert.equal(itmTone(5, 3), 'itm')
  assert.equal(itmTone(-3, 1), 'otm')
  assert.equal(itmTone(null, 1), 'unknown')
})

test('fmtItm says which side of the strike the stock is', () => {
  assert.equal(fmtItm(2), '2.0% ITM')
  assert.equal(fmtItm(-3.25), '3.3% OTM')
  assert.equal(fmtItm(null), '—')
})
```

- [ ] **Step 2: Run it and watch it fail.**

Run: `cd frontend && npm test`
Expected: `swing.test.mjs` fails with `ERR_MODULE_NOT_FOUND ... src/utils/swing.js`. The 9 `strategyConfig` tests still pass.

- [ ] **Step 3: Implement** `frontend/src/utils/swing.js`:

```js
/**
 * Pure helpers for the swing / wheel approval UI (PendingSignalsCard and
 * WheelPanel). No Vue and no fetch, so the rules can be exercised with
 * `npm test` (node --test) without mounting a component.
 *
 * Shapes: the SwingSignals document is section 5 of
 * docs/superpowers/plans/2026-09-24-swing-port-interfaces.md; the wheel
 * payload is the Contract addendum of plan C
 * (docs/superpowers/plans/2026-09-24-swing-port-C-ui.md). If plan B ships a
 * different wheel shape, parseWheelPayload is the only reader to change.
 */

const LANE_BY_STRATEGY_ID = { strategy_swing: 'swing', strategy_wheel: 'wheel' }

function canonicalStrategyId(value) {
  // "StrategySwing" -> "strategy_swing"; "strategy_swing" is unchanged.
  return String(value ?? '').trim().replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase()
}

/** Which approval lanes the instance's strategy document carries. */
export function swingLanesOf(strategyDoc) {
  const subs = Array.isArray(strategyDoc?.strategies) ? strategyDoc.strategies : []
  const lanes = { swing: false, wheel: false }
  for (const sub of subs) {
    const lane = LANE_BY_STRATEGY_ID[canonicalStrategyId(sub?.strategy)]
    if (lane) lanes[lane] = true
  }
  return { ...lanes, any: lanes.swing || lanes.wheel }
}

/**
 * GET /instances/{id}/swing/signals?status=pending -> pending rows, newest
 * first. Accepts a bare list or {signals: [...]}; drops anything not pending
 * in case an older API build ignores the status filter.
 */
export function normalizeSignalList(payload) {
  const rows = Array.isArray(payload) ? payload : (Array.isArray(payload?.signals) ? payload.signals : [])
  return rows
    .filter(r => r && typeof r === 'object' && r.id && String(r.status ?? 'pending') === 'pending')
    .sort((a, b) => String(b.created_at ?? '').localeCompare(String(a.created_at ?? '')))
}

/** A decision this page recorded is final; a poll that raced it must not re-add the card. */
export function withoutDecided(signals, decidedIds) {
  return signals.filter(s => !decidedIds.has(s.id))
}

export function scoreTone(score) {
  if (score == null || score === '') return 'unknown'
  const n = Number(score)
  if (!Number.isFinite(n)) return 'unknown'
  if (n >= 75) return 'high'
  if (n >= 50) return 'mid'
  return 'low'
}

export function joinKeyRisks(keyRisks) {
  if (!Array.isArray(keyRisks)) return ''
  return keyRisks.map(r => String(r ?? '').trim()).filter(Boolean).join(' · ')
}

export const REASONING_PREVIEW_CHARS = 280

/** Collapsed reasoning: cut on a word boundary when one is close, else hard. */
export function reasoningPreview(text, limit = REASONING_PREVIEW_CHARS) {
  const full = String(text ?? '').trim()
  if (full.length <= limit) return { text: full, truncated: false }
  const cut = full.slice(0, limit)
  const lastSpace = cut.lastIndexOf(' ')
  const head = lastSpace > limit * 0.6 ? cut.slice(0, lastSpace) : cut
  return { text: `${head.trimEnd()}…`, truncated: true }
}

export const DECISION_LABELS = { approve: 'Approve', approve_half: 'Approve ½', reject: 'Reject' }

/** Approve half exists for swing entries only; the wheel sizes in whole contracts. */
export function decisionsFor(signal) {
  return signal?.lane === 'swing' ? ['approve', 'approve_half', 'reject'] : ['approve', 'reject']
}

export function fmtUsd(value) {
  if (value == null || value === '') return '—'
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return (n < 0 ? '-$' : '$') + Math.abs(n).toLocaleString('en-US', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })
}

function fmtCount(value) {
  if (value == null || value === '') return '—'
  const n = Number(value)
  return Number.isFinite(n) ? String(Math.trunc(n)) : '—'
}

function finiteOrNull(value) {
  if (value == null || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

/** The proposal grid on a card: [{label, value, mono?}]. */
export function proposalRows(signal) {
  const p = signal?.proposal && typeof signal.proposal === 'object' ? signal.proposal : {}
  if (signal?.lane === 'wheel') {
    const qty = finiteOrNull(p.qty)
    const premium = finiteOrNull(p.premium_est)
    const strike = finiteOrNull(p.strike)
    const credit = qty != null && premium != null ? premium * 100 * qty : null
    const collateral = qty != null && strike != null ? strike * 100 * qty : null
    return [
      { label: 'Contract', value: p.contract ? String(p.contract) : '—', mono: true },
      { label: 'Strike', value: fmtUsd(p.strike) },
      { label: 'Expiry', value: p.expiry ? String(p.expiry) : '—' },
      { label: 'Qty', value: fmtCount(p.qty) },
      { label: 'Limit', value: fmtUsd(p.limit_price) },
      { label: 'Premium', value: credit == null ? fmtUsd(p.premium_est) : `${fmtUsd(p.premium_est)} (${fmtUsd(credit)})` },
      { label: 'Collateral', value: fmtUsd(collateral) },
    ]
  }
  return [
    { label: 'Entry', value: fmtUsd(p.entry) },
    { label: 'Stop', value: fmtUsd(p.stop) },
    { label: 'Target', value: fmtUsd(p.target) },
    { label: 'Shares', value: fmtCount(p.shares) },
  ]
}

export function confirmPrompt(signal, decision) {
  const sym = signal?.symbol || 'this signal'
  if (decision === 'approve') return `Approve ${sym}? The order is rebuilt at the live price and placed within seconds.`
  if (decision === 'approve_half') return `Approve ${sym} at half size? The order is rebuilt at the live price and placed within seconds.`
  return `Reject ${sym}? Decisions are final.`
}

export function decisionSuccessMessage(signal, decision) {
  const sym = signal?.symbol || 'the signal'
  if (decision === 'approve') return `Approved ${sym}. The order goes out on the broker's next command poll.`
  if (decision === 'approve_half') return `Approved ${sym} at half size. The order goes out on the broker's next command poll.`
  return `Rejected ${sym}.`
}

/** FastAPI `detail` may be a string or a list of {msg|message}. */
export function detailText(detail) {
  if (detail == null) return ''
  if (typeof detail === 'string') return detail.trim()
  if (Array.isArray(detail)) {
    return detail.map(d => (d && typeof d === 'object' ? (d.msg ?? d.message ?? JSON.stringify(d)) : String(d))).join('; ')
  }
  return String(detail)
}

/**
 * What a failed POST .../decision means for the card.
 * 400/404/409: the signal is no longer pending (another device decided, or it
 * is gone). Remove the card and show the server's reason; if it IS still
 * pending, the next poll brings it back.
 */
export function classifyDecisionFailure(status, detail) {
  const text = detailText(detail)
  if (status === 401) {
    return { kind: 'unauthorized', removeCard: false, stopPolling: true, message: 'Session expired — please sign in again.' }
  }
  if (status === 403) {
    return { kind: 'forbidden', removeCard: false, stopPolling: false, message: text || 'You are not allowed to decide this signal.' }
  }
  if (status === 400 || status === 404 || status === 409) {
    return { kind: 'stale', removeCard: true, stopPolling: false, message: text || 'This signal is no longer pending — it was decided elsewhere.' }
  }
  if (!status) {
    return { kind: 'failed', removeCard: false, stopPolling: false, message: text || 'Could not reach the server.' }
  }
  return { kind: 'failed', removeCard: false, stopPolling: false, message: text || `Request failed (${status})` }
}

/**
 * Synchronous per-id latch. `disabled` on a button only takes effect after
 * Vue re-renders; two clicks inside one tick would both get through without
 * this.
 */
export function createInFlightGuard() {
  const ids = new Set()
  return {
    tryAcquire(id) {
      if (ids.has(id)) return false
      ids.add(id)
      return true
    },
    release(id) { ids.delete(id) },
    has(id) { return ids.has(id) },
  }
}

/** GET /instances/{id}/wheel, per the plan C Contract addendum. */
export function parseWheelPayload(payload) {
  const src = payload && typeof payload === 'object' ? payload : {}
  const openPuts = (Array.isArray(src.open_puts) ? src.open_puts : [])
    .filter(r => r && typeof r === 'object')
    .map(r => ({
      contract: String(r.contract ?? ''),
      underlying: String(r.underlying ?? ''),
      strike: finiteOrNull(r.strike),
      expiry: String(r.expiry ?? ''),
      qty: finiteOrNull(r.qty),
      avgEntryPrice: finiteOrNull(r.avg_entry_price),
      currentPrice: finiteOrNull(r.current_price),
      underlyingPrice: finiteOrNull(r.underlying_price),
      itmPct: finiteOrNull(r.itm_pct),
      dte: finiteOrNull(r.dte),
      collateral: finiteOrNull(r.collateral),
      unrealizedPl: finiteOrNull(r.unrealized_pl),
    }))
  const recentScans = (Array.isArray(src.recent_scans) ? src.recent_scans : [])
    .filter(r => r && typeof r === 'object')
    .map(r => ({
      id: String(r.id ?? ''),
      session: String(r.session ?? ''),
      createdAt: String(r.created_at ?? ''),
      symbol: String(r.symbol ?? ''),
      stockPrice: finiteOrNull(r.stock_price),
      strike: finiteOrNull(r.strike),
      expiry: String(r.expiry ?? ''),
      premiumEst: finiteOrNull(r.premium_est),
      score: finiteOrNull(r.score),
      recommendation: String(r.recommendation ?? ''),
      status: String(r.status ?? ''),
      skipReason: r.skip_reason ? String(r.skip_reason) : '',
    }))
  return {
    openPuts,
    collateralTotal: finiteOrNull(src.collateral_total),
    cash: finiteOrNull(src.cash),
    recentScans,
  }
}

/** 'alert' is exactly the set the 15:45 monitor buys back (spec section 5.2). */
export function itmTone(itmPct, dte) {
  if (itmPct == null) return 'unknown'
  if (itmPct >= 10 || (itmPct >= 5 && dte != null && dte <= 2) || (itmPct > 0 && dte === 0)) return 'alert'
  if (itmPct > 0) return 'itm'
  return 'otm'
}

export function fmtItm(itmPct) {
  if (itmPct == null) return '—'
  return itmPct > 0 ? `${itmPct.toFixed(1)}% ITM` : `${Math.abs(itmPct).toFixed(1)}% OTM`
}
```

- [ ] **Step 4: Run the tests.**

Run: `cd frontend && npm test`
Expected: `ℹ pass 27`, `ℹ fail 0` (9 from Task 1 and 18 new).

- [ ] **Step 5: Commit.** Run `mcp__gitnexus__detect_changes()` first. Expected: one new file, no existing symbol changed.

```bash
git add frontend/src/utils/swing.js frontend/tests/swing.test.mjs
git commit -F - <<'EOF'
feat(web): pure helpers for swing signal approvals and the wheel panel

Lane detection from the strategy document, signal list normalisation,
proposal formatting for swing and wheel, decision error classes (400/404/409
remove the card, 401 stops polling, 403 keeps it), a synchronous per-signal
latch against double clicks, and the wheel payload reader. All pure and
covered by node --test.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 3: `PendingSignalsCard.vue`, `WheelPanel.vue` and the instance page

**Files:**
- Create: `frontend/src/components/swing/PendingSignalsCard.vue`
- Create: `frontend/src/components/swing/WheelPanel.vue`
- Modify: `frontend/src/views/InstanceDetailView.vue:5` (imports), `:30-31` (after `isStockInstance`), `:1697-1706` (after the info grid that holds `LiveReadinessCard`)

**Interfaces:**
- Consumes: Task 2's exports; `getToken()` from `frontend/src/utils/auth.js`; `API_BASE` and `instanceId` (a computed string) in `InstanceDetailView.vue`.
- Produces:
  - `<PendingSignalsCard :instance-id :api-base>`, with props `instanceId: String` and `apiBase: String`, both required;
  - `<WheelPanel :instance-id :api-base>`, with the same props;
  - `swingLanes`, a computed in `InstanceDetailView.vue`.

These follow the `LearningView.vue` approval pattern (markup 661-730, `decide()` 282-296): `fetch` with `authHeaders()`, 401 stops polling, and the FastAPI `detail` is surfaced. Unlike LearningView, a decision here is final and moves money on paper. So there is a confirm step, a synchronous double-click latch, and a card that goes on 400/404/409.

- [ ] **Step 0: Impact.** Run `mcp__gitnexus__impact({target: "InstanceDetailView.vue", direction: "upstream"})`. It is a route view; confirm with `grep -rn "InstanceDetailView" frontend/src/router`. Only the router imports it. The edits add a computed and a template block, and change no existing function. Risk is LOW.

- [ ] **Step 1: Create** `frontend/src/components/swing/PendingSignalsCard.vue`:

```vue
<!-- frontend/src/components/swing/PendingSignalsCard.vue
     AI-scored swing and wheel candidates in the review band (from the review
     threshold up to the auto-approve threshold) wait here for a human.

     An approval makes the API enqueue a submit_order live command, and the
     broker rebuilds the order at the live price, so the numbers on a card
     are the proposal, not the fill.

     Decisions are final (swing_trader.approvals.decide). So every button goes
     through a confirm step, is disabled while its request is in flight, and a
     synchronous guard drops a second click that lands before Vue re-renders.
     A 400/404/409 means the signal is no longer pending, usually because
     another device decided first. The card goes, the server's reason is shown,
     and the next poll brings the card back only if it really is still pending. -->
<template>
  <section class="glass-card rounded-2xl p-5">
    <div class="flex items-center justify-between mb-4 gap-2">
      <p class="text-xs font-bold uppercase tracking-widest text-slate-500">
        Pending AI Signals <span class="text-slate-700 ml-1">({{ signals.length }})</span>
      </p>
      <button
        @click="load"
        :disabled="loading"
        class="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-400 hover:bg-slate-800 px-2 py-1 rounded-lg border border-slate-700 transition-colors disabled:opacity-40"
      >
        <span class="material-symbols-outlined text-[13px]" :class="loading ? 'animate-spin' : ''">
          {{ loading ? 'progress_activity' : 'refresh' }}
        </span>
        Refresh
      </button>
    </div>

    <div v-if="notice" class="mb-3 rounded-lg border px-3 py-2 text-xs" :class="noticeClass">
      {{ notice.text }}
    </div>
    <div v-if="loadError" class="mb-3 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
      {{ loadError }}
    </div>

    <div v-if="!loaded && loading" class="text-xs text-slate-500">Loading…</div>
    <div
      v-else-if="!signals.length"
      class="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-6 text-center"
    >
      <p class="text-sm text-slate-400">Nothing waiting for review.</p>
      <p class="text-xs text-slate-600 mt-1">
        Scores from the review threshold up to the auto-approve threshold wait here.
      </p>
    </div>

    <div v-else class="space-y-2">
      <div
        v-for="s in signals"
        :key="s.id"
        class="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3"
      >
        <div class="flex items-center gap-2 flex-wrap">
          <span class="text-base font-black text-slate-100 tracking-wide font-mono">{{ s.symbol }}</span>
          <span class="px-2 py-0.5 rounded-full text-[10px] font-bold border uppercase" :class="laneClass(s.lane)">
            {{ s.lane }}
          </span>
          <span
            class="px-2 py-0.5 rounded-full text-[10px] font-bold border tabular-nums"
            :class="scoreClass(s.score)"
            :title="s.recommendation || ''"
          >{{ s.score ?? '—' }}</span>
          <span class="text-[11px] text-slate-600">
            session {{ s.session || '—' }} · {{ fmtWhen(s.created_at) }}
          </span>
        </div>

        <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-3">
          <div v-for="row in proposalRows(s)" :key="row.label" class="min-w-0">
            <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider">{{ row.label }}</div>
            <div
              class="text-xs font-bold text-slate-200 tabular-nums truncate"
              :class="row.mono ? 'font-mono' : ''"
              :title="row.value"
            >{{ row.value }}</div>
          </div>
        </div>

        <p v-if="s.reasoning" class="text-xs text-slate-300 mt-3 leading-relaxed whitespace-pre-line break-words">
          {{ expanded[s.id] ? String(s.reasoning).trim() : reasoningPreview(s.reasoning).text }}
          <button
            v-if="reasoningPreview(s.reasoning).truncated"
            @click="toggleExpanded(s.id)"
            class="ml-1 text-[11px] font-semibold text-sky-400 hover:underline"
          >{{ expanded[s.id] ? 'Show less' : 'Show more' }}</button>
        </p>
        <p v-if="joinKeyRisks(s.key_risks)" class="text-[11px] text-amber-300/80 mt-2 break-words">
          Risks: {{ joinKeyRisks(s.key_risks) }}
        </p>

        <!-- Confirm step: nothing is sent until this second click. -->
        <div
          v-if="confirming[s.id]"
          class="mt-3 rounded-lg border px-3 py-2"
          :class="confirming[s.id] === 'reject'
            ? 'border-slate-700 bg-slate-800/40'
            : 'border-emerald-500/25 bg-emerald-500/5'"
        >
          <p class="text-xs text-slate-200">{{ confirmPrompt(s, confirming[s.id]) }}</p>
          <input
            v-model="reasons[s.id]"
            maxlength="500"
            placeholder="Reason (optional)"
            :disabled="!!deciding[s.id]"
            class="mt-2 w-full rounded-lg bg-slate-900 border border-slate-700 px-2 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-slate-500"
          />
          <div class="flex gap-2 mt-2 flex-wrap">
            <button
              @click="submit(s)"
              :disabled="!!deciding[s.id]"
              class="px-3 py-1.5 rounded-lg text-xs font-semibold border disabled:opacity-50"
              :class="decisionClass(confirming[s.id])"
            >{{ deciding[s.id] ? 'Working…' : `Confirm ${DECISION_LABELS[confirming[s.id]]}` }}</button>
            <button
              @click="cancelConfirm(s.id)"
              :disabled="!!deciding[s.id]"
              class="px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-700 bg-slate-800/60 text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >Cancel</button>
          </div>
        </div>
        <div v-else class="flex gap-2 mt-3 flex-wrap">
          <button
            v-for="d in decisionsFor(s)"
            :key="d"
            @click="startConfirm(s.id, d)"
            :disabled="!!deciding[s.id]"
            class="px-3 py-1.5 rounded-lg text-xs font-semibold border disabled:opacity-50"
            :class="decisionClass(d)"
          >{{ DECISION_LABELS[d] }}</button>
        </div>
      </div>
    </div>

    <p class="text-[10px] text-slate-600 mt-3 leading-relaxed">
      Approval rebuilds the order at the live price: shares, stop and target for swing, strike and expiry for the wheel.
    </p>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { getToken } from '../../utils/auth.js'
import {
  DECISION_LABELS,
  classifyDecisionFailure,
  confirmPrompt,
  createInFlightGuard,
  decisionSuccessMessage,
  decisionsFor,
  detailText,
  joinKeyRisks,
  normalizeSignalList,
  proposalRows,
  reasoningPreview,
  scoreTone,
  withoutDecided,
} from '../../utils/swing.js'

const POLL_MS = 30000

const props = defineProps({
  instanceId: { type: String, required: true },
  apiBase: { type: String, required: true },
})

const signals = ref([])
const loading = ref(false)
const loaded = ref(false)
const loadError = ref('')
const notice = ref(null)      // { tone: 'ok' | 'warn' | 'error', text }
const confirming = ref({})    // signal id -> decision awaiting its confirm click
const deciding = ref({})      // signal id -> true while the POST is in flight
const reasons = ref({})
const expanded = ref({})
const guard = createInFlightGuard()
const decided = new Set()     // ids this page decided; a racing poll never re-adds them
let pollTimer = null
let noticeTimer = null

function authHeaders() {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

function signalsUrl() {
  return `${props.apiBase}/instances/${encodeURIComponent(props.instanceId)}/swing/signals`
}

function fmtWhen(value) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString()
}

function omit(map, id) {
  const next = { ...map }
  delete next[id]
  return next
}

function showNotice(tone, text) {
  notice.value = { tone, text }
  clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => { notice.value = null }, 8000)
}

const noticeClass = computed(() => ({
  ok: 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300',
  warn: 'border-amber-500/20 bg-amber-500/10 text-amber-300',
  error: 'border-rose-500/20 bg-rose-500/10 text-rose-300',
}[notice.value?.tone] || ''))

function laneClass(lane) {
  return lane === 'wheel'
    ? 'text-violet-300 bg-violet-500/10 border-violet-500/20'
    : 'text-sky-400 bg-sky-500/10 border-sky-500/20'
}

function scoreClass(score) {
  return {
    high: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
    mid: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
    low: 'text-rose-400 bg-rose-500/10 border-rose-500/20',
    unknown: 'text-slate-400 bg-slate-500/10 border-slate-700',
  }[scoreTone(score)]
}

function decisionClass(decision) {
  return decision === 'reject'
    ? 'border-slate-700 bg-slate-800/60 text-slate-300 hover:bg-slate-800'
    : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20'
}

function toggleExpanded(id) {
  expanded.value = { ...expanded.value, [id]: !expanded.value[id] }
}

function removeCard(id) {
  signals.value = signals.value.filter(s => s.id !== id)
  confirming.value = omit(confirming.value, id)
  reasons.value = omit(reasons.value, id)
}

async function load() {
  if (loading.value) return
  loading.value = true
  try {
    const res = await fetch(`${signalsUrl()}?status=pending`, { headers: authHeaders() })
    if (res.status === 401) {
      stopPolling()
      loadError.value = 'Session expired — please sign in again.'
      return
    }
    if (!res.ok) {
      let detail = ''
      try { detail = detailText((await res.json())?.detail) } catch { /* keep */ }
      throw new Error(detail || `Could not load signals (${res.status})`)
    }
    const next = withoutDecided(normalizeSignalList(await res.json()), decided)
    const live = new Set(next.map(s => s.id))
    // A card that vanished server-side takes its half-finished confirm with it.
    for (const id of Object.keys(confirming.value)) {
      if (!live.has(id) && !deciding.value[id]) confirming.value = omit(confirming.value, id)
    }
    signals.value = next
    loadError.value = ''
    loaded.value = true
  } catch (e) {
    loadError.value = e?.message || 'Could not load signals'
  } finally {
    loading.value = false
  }
}

function startConfirm(id, decision) {
  if (deciding.value[id]) return
  confirming.value = { ...confirming.value, [id]: decision }
}

function cancelConfirm(id) {
  if (deciding.value[id]) return
  confirming.value = omit(confirming.value, id)
}

async function submit(signal) {
  const decision = confirming.value[signal.id]
  if (!decision || !guard.tryAcquire(signal.id)) return
  deciding.value = { ...deciding.value, [signal.id]: true }
  try {
    const body = { decision }
    const reason = String(reasons.value[signal.id] || '').trim()
    if (reason) body.reason = reason
    const res = await fetch(`${signalsUrl()}/${encodeURIComponent(signal.id)}/decision`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(body),
    })
    if (res.ok) {
      decided.add(signal.id)
      removeCard(signal.id)
      showNotice('ok', decisionSuccessMessage(signal, decision))
      return
    }
    let detail = ''
    try { detail = (await res.json())?.detail } catch { /* keep */ }
    const verdict = classifyDecisionFailure(res.status, detail)
    if (verdict.stopPolling) {
      stopPolling()
      loadError.value = verdict.message
    }
    if (verdict.removeCard) removeCard(signal.id)
    showNotice(verdict.kind === 'stale' ? 'warn' : 'error', verdict.message)
  } catch (e) {
    showNotice('error', classifyDecisionFailure(0, e?.message).message)
  } finally {
    guard.release(signal.id)
    deciding.value = omit(deciding.value, signal.id)
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(load, POLL_MS)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(() => {
  load()
  startPolling()
})

onUnmounted(() => {
  stopPolling()
  clearTimeout(noticeTimer)
})

watch(() => props.instanceId, (next, prev) => {
  if (next === prev) return
  signals.value = []
  loaded.value = false
  loadError.value = ''
  confirming.value = {}
  reasons.value = {}
  decided.clear()
  load()
  startPolling()
})
</script>
```

- [ ] **Step 2: Create** `frontend/src/components/swing/WheelPanel.vue`:

```vue
<!-- frontend/src/components/swing/WheelPanel.vue
     The wheel lane's book: open cash-secured puts, the collateral they tie
     up, how far each is from being assigned, and the latest weekly scans.

     Read-only. The lane buys puts back by itself (the 15:45 ET monitor and the
     Monday 2x-premium check), so the ITM column is coloured by exactly those
     rules: red means the monitor will buy this put back on its next pass.
     GET /instances/{id}/wheel; the shape is the plan C Contract addendum. -->
<template>
  <section class="glass-card rounded-2xl p-5">
    <div class="flex items-center justify-between mb-4 gap-2">
      <p class="text-xs font-bold uppercase tracking-widest text-slate-500">
        Wheel <span class="text-slate-700 ml-1">({{ wheel.openPuts.length }} open)</span>
      </p>
      <button
        @click="load"
        :disabled="loading"
        class="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-400 hover:bg-slate-800 px-2 py-1 rounded-lg border border-slate-700 transition-colors disabled:opacity-40"
      >
        <span class="material-symbols-outlined text-[13px]" :class="loading ? 'animate-spin' : ''">
          {{ loading ? 'progress_activity' : 'refresh' }}
        </span>
        Refresh
      </button>
    </div>

    <div v-if="loadError" class="mb-3 rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
      {{ loadError }}
    </div>
    <div v-if="!loaded && loading" class="text-xs text-slate-500">Loading…</div>

    <template v-if="loaded">
      <div class="grid grid-cols-3 gap-2 mb-4">
        <div class="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
          <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Open puts</div>
          <div class="text-sm font-bold text-slate-100 tabular-nums">{{ wheel.openPuts.length }}</div>
        </div>
        <div class="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
          <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Collateral</div>
          <div class="text-sm font-bold text-slate-100 tabular-nums">{{ fmtUsd(wheel.collateralTotal) }}</div>
        </div>
        <div class="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
          <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Cash</div>
          <div class="text-sm font-bold text-slate-100 tabular-nums">{{ fmtUsd(wheel.cash) }}</div>
        </div>
      </div>

      <div v-if="!wheel.openPuts.length" class="text-xs text-slate-600 italic mb-4">No open puts.</div>
      <div v-else class="rounded-lg border border-slate-800 bg-slate-900/40 overflow-x-auto mb-4">
        <table class="w-full text-xs">
          <thead>
            <tr class="text-slate-500 border-b border-slate-800">
              <th class="text-left font-medium px-3 py-2">Contract</th>
              <th class="text-right font-medium px-3 py-2">Qty</th>
              <th class="text-right font-medium px-3 py-2">Entry</th>
              <th class="text-right font-medium px-3 py-2">Mark</th>
              <th class="text-right font-medium px-3 py-2">ITM</th>
              <th class="text-right font-medium px-3 py-2">DTE</th>
              <th class="text-right font-medium px-3 py-2">Collateral</th>
              <th class="text-right font-medium px-3 py-2">P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in wheel.openPuts" :key="p.contract" class="border-b border-slate-800/60 last:border-0">
              <td class="px-3 py-2">
                <div class="text-slate-200 font-semibold">{{ p.underlying }} {{ fmtUsd(p.strike) }} P</div>
                <div class="text-[10px] text-slate-600 font-mono">{{ p.contract }} · {{ p.expiry }}</div>
              </td>
              <td class="px-3 py-2 text-right text-slate-300 tabular-nums">{{ p.qty ?? '—' }}</td>
              <td class="px-3 py-2 text-right text-slate-300 tabular-nums">{{ fmtUsd(p.avgEntryPrice) }}</td>
              <td class="px-3 py-2 text-right text-slate-300 tabular-nums">{{ fmtUsd(p.currentPrice) }}</td>
              <td class="px-3 py-2 text-right tabular-nums font-semibold" :class="itmClass(p)">{{ fmtItm(p.itmPct) }}</td>
              <td class="px-3 py-2 text-right text-slate-300 tabular-nums">{{ p.dte ?? '—' }}</td>
              <td class="px-3 py-2 text-right text-slate-300 tabular-nums">{{ fmtUsd(p.collateral) }}</td>
              <td class="px-3 py-2 text-right tabular-nums" :class="pnlClass(p.unrealizedPl)">{{ fmtUsd(p.unrealizedPl) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="text-[11px] font-bold uppercase tracking-widest text-slate-500 mb-2">Recent scans</p>
      <div v-if="!wheel.recentScans.length" class="text-xs text-slate-600 italic">No scans recorded yet.</div>
      <div v-else class="space-y-1.5">
        <div
          v-for="scan in wheel.recentScans"
          :key="scan.id || `${scan.session}-${scan.symbol}`"
          class="flex items-start justify-between gap-3 text-xs rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2"
        >
          <div class="min-w-0">
            <span class="font-mono font-semibold text-slate-200">{{ scan.symbol }}</span>
            <span class="text-slate-500 ml-2">{{ fmtUsd(scan.strike) }} P · {{ scan.expiry || '—' }}</span>
            <p v-if="scan.skipReason" class="text-[11px] text-slate-600 mt-0.5 break-words">{{ scan.skipReason }}</p>
          </div>
          <div class="shrink-0 text-right">
            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold border uppercase" :class="scanClass(scan.status)">
              {{ scan.status || '—' }}
            </span>
            <div class="text-[10px] text-slate-600 mt-0.5">
              {{ scan.session }}<span v-if="scan.score != null"> · score {{ scan.score }}</span>
            </div>
          </div>
        </div>
      </div>
    </template>
  </section>
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { getToken } from '../../utils/auth.js'
import { detailText, fmtItm, fmtUsd, itmTone, parseWheelPayload } from '../../utils/swing.js'

// The wheel changes a few times a day; a minute is plenty.
const POLL_MS = 60000

const props = defineProps({
  instanceId: { type: String, required: true },
  apiBase: { type: String, required: true },
})

const wheel = ref(parseWheelPayload(null))
const loading = ref(false)
const loaded = ref(false)
const loadError = ref('')
let pollTimer = null

function authHeaders() {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

async function load() {
  if (loading.value) return
  loading.value = true
  try {
    const res = await fetch(
      `${props.apiBase}/instances/${encodeURIComponent(props.instanceId)}/wheel`,
      { headers: authHeaders() },
    )
    if (res.status === 401 || res.status === 404) {
      // 401: the session is gone. 404: this API build has no wheel route yet.
      // Either way, polling again will not change the answer.
      stopPolling()
      loadError.value = res.status === 401
        ? 'Session expired — please sign in again.'
        : 'This API build has no wheel endpoint yet.'
      return
    }
    if (!res.ok) {
      let detail = ''
      try { detail = detailText((await res.json())?.detail) } catch { /* keep */ }
      throw new Error(detail || `Could not load the wheel (${res.status})`)
    }
    wheel.value = parseWheelPayload(await res.json())
    loadError.value = ''
    loaded.value = true
  } catch (e) {
    loadError.value = e?.message || 'Could not load the wheel'
  } finally {
    loading.value = false
  }
}

function itmClass(put) {
  return {
    alert: 'text-rose-400',
    itm: 'text-amber-400',
    otm: 'text-emerald-400',
    unknown: 'text-slate-500',
  }[itmTone(put.itmPct, put.dte)]
}

function pnlClass(value) {
  if (value == null || value === 0) return 'text-slate-400'
  return value > 0 ? 'text-emerald-400' : 'text-rose-400'
}

function scanClass(status) {
  return {
    placed: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
    pending: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
    rejected: 'text-rose-400 bg-rose-500/10 border-rose-500/20',
  }[status] || 'text-slate-400 bg-slate-500/10 border-slate-700'
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(load, POLL_MS)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(() => {
  load()
  startPolling()
})

onUnmounted(stopPolling)

watch(() => props.instanceId, (next, prev) => {
  if (next === prev) return
  wheel.value = parseWheelPayload(null)
  loaded.value = false
  loadError.value = ''
  load()
  startPolling()
})
</script>
```

- [ ] **Step 3: Wire both into** `frontend/src/views/InstanceDetailView.vue`, with three edits.

3a. Replace
```js
import LiveReadinessCard from '../components/LiveReadinessCard.vue'
```
with
```js
import LiveReadinessCard from '../components/LiveReadinessCard.vue'
import PendingSignalsCard from '../components/swing/PendingSignalsCard.vue'
import WheelPanel from '../components/swing/WheelPanel.vue'
import { swingLanesOf } from '../utils/swing.js'
```

3b. Replace
```js
const isStockInstance = computed(
  () => !['crypto', 'kalshi'].includes(String(inst.value?.kind ?? '').toLowerCase()))
```
with
```js
const isStockInstance = computed(
  () => !['crypto', 'kalshi'].includes(String(inst.value?.kind ?? '').toLowerCase()))

// The swing / wheel approval cards appear only on an equity instance whose
// strategy document carries one of those lanes (spec 2026-09-24 section 10).
const swingLanes = computed(() => swingLanesOf(inst.value?.strategy))
```

3c. Replace (the end of the info grid, right after `LiveReadinessCard`)
```html
            @waived="fetchInstance"
            @revoked="fetchInstance"
          />
        </div>

        <!-- ── Stocks ──
```
with
```html
            @waived="fetchInstance"
            @revoked="fetchInstance"
          />
        </div>

        <!-- ── Swing / wheel lanes ───────────────────────────────────────── -->
        <div
          v-if="isStockInstance && swingLanes.any"
          class="grid grid-cols-1 xl:grid-cols-2 gap-5 mb-6"
        >
          <PendingSignalsCard :instance-id="instanceId" :api-base="API_BASE" />
          <WheelPanel v-if="swingLanes.wheel" :instance-id="instanceId" :api-base="API_BASE" />
        </div>

        <!-- ── Stocks ──
```

- [ ] **Step 4: Run the gate.**

Run: `cd frontend && npm test && npm run build`
Expected: `ℹ pass 27`, then `✓ built in` with no error.

- [ ] **Step 5: Manual check.** This needs plan B's routes. If they are not on the branch yet, do everything except items 3-6 now, and finish once B lands.
  1. `npm run dev`. Open an equity instance whose strategy has `strategy_eb` only: no swing cards.
  2. Open `swing-paper` (or any instance whose document lists `strategy_swing` or `strategy_wheel`). "Pending AI Signals" shows. "Wheel" shows only when `strategy_wheel` is listed.
  3. **Double click:** click Approve, then double-click "Confirm Approve". The Network tab shows exactly one POST to `.../decision`.
  4. **Decided elsewhere:** open the page in two tabs and reject a signal in tab A. In tab B, approve the same signal: an amber notice shows the server's reason and the card disappears. It does not return after 30 s.
  5. **Session expiry:** run `localStorage.clear()` in devtools, or sign out in another tab, then approve. You see "Session expired — please sign in again.", and the Network tab shows no more `swing/signals` polls.
  6. **Long reasoning:** a signal with more than 280 characters of reasoning shows "Show more", and the card never scrolls sideways at a 375 px viewport.
  7. With the backend lacking the routes, the wheel panel says "This API build has no wheel endpoint yet." and stops polling.

- [ ] **Step 6: Commit.** Run `mcp__gitnexus__detect_changes()` first. Expected: two new SFCs and `InstanceDetailView.vue` only.

```bash
git add frontend/src/components/swing/PendingSignalsCard.vue frontend/src/components/swing/WheelPanel.vue frontend/src/views/InstanceDetailView.vue
git commit -F - <<'EOF'
feat(web): pending AI signal approvals and wheel panel on the instance page

PendingSignalsCard lists swing and wheel signals waiting for review, with
Approve, Approve half (swing only) and Reject behind a confirm step, a
per-signal in-flight latch, and a 30 second poll. A decision that another
device already made removes the card and shows the server's reason; an
expired session stops polling. WheelPanel shows open puts, collateral, ITM
distance, days to expiry and recent scans. Both appear only when the
instance's strategy document has a swing or wheel lane.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 4: Options in Live Trading (`optionPositions.js` + `LiveTradingView.vue`)

**Files:**
- Create: `frontend/src/utils/optionPositions.js`
- Test: `frontend/tests/optionPositions.test.mjs`
- Modify: `frontend/src/views/LiveTradingView.vue:8` (import), `:438` (`positionSymbols`), `:1155-1199` (Recent Executions), `:1203-1295` (Active Positions)

**Interfaces:**
- Consumes: the `npm test` script (Task 1); the live-state position fields from plan A-live (contract addendum item 4).
- Produces:
  - `OPTION_MULTIPLIER = 100`
  - `parseOccSymbol(symbol) -> {underlying, expiry, optionType, strike} | null`
  - `isOptionRow(row) -> boolean`
  - `contractMultiplier(row) -> number`
  - `isShortPosition(row) -> boolean`
  - `quantityLabel(row) -> 'Contracts'|'Shares'`
  - `displayQuantity(row, maxFractionDigits?) -> string`
  - `tradeTotal(trade) -> number`
  - `canClosePosition(row) -> boolean`
  - `historicalsSymbols(positions) -> string[]`
  - `describeOption(row) -> string`

**Decision: Close is hidden for every option, not only short ones.** Spec §10 says "Close hidden for short options". `broker.py`'s `close_position` handler (around line 10025) reads only `adapter._positions`, the equity book. So a Close on any option, long or short, fails with "no open long position". The wheel lane never holds a long option, so this matches the spec for every row the lanes produce.

- [ ] **Step 0: Impact.** Run `mcp__gitnexus__impact` upstream on `fetchPositionHistoricals` and `positionSymbols` (`frontend/src/views/LiveTradingView.vue`). Confirm with `grep -n "positionSymbols\|positionSymbolsKey" frontend/src/views/LiveTradingView.vue`: only the `watch` at the bottom and `fetchPositionHistoricals` read them. For equity-only accounts every change here is behaviour-neutral. Risk is LOW.

- [ ] **Step 1: Write the failing test** `frontend/tests/optionPositions.test.mjs`:

```js
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  OPTION_MULTIPLIER,
  canClosePosition,
  contractMultiplier,
  describeOption,
  displayQuantity,
  historicalsSymbols,
  isOptionRow,
  isShortPosition,
  parseOccSymbol,
  quantityLabel,
  tradeTotal,
} from '../src/utils/optionPositions.js'

// live-state position after plan A-live: Alpaca's own current_price may be null.
const SHORT_PUT = {
  symbol: 'APH261002P00130000', qty: -1, avg_entry_price: 1.23,
  last_price: null, market_value: null, unrealized_pnl: null, unrealized_pnl_pct: null,
  asset_class: 'us_option', side: 'short', multiplier: 100,
  underlying: 'APH', strike: 130, expiry: '2026-10-02',
}
const STOCK = { symbol: 'AAPL', qty: 12, avg_entry_price: 190, last_price: 200, market_value: 2400 }
// recent_trades rows carry no asset_class: the OCC shape is the only signal.
const OPTION_TRADE = { symbol: 'APH261002P00130000', side: 'sell', qty: 1, price: 1.25 }
const STOCK_TRADE = { symbol: 'AAPL', side: 'buy', qty: 12, price: 200 }

test('parseOccSymbol reads root, expiry, type and strike', () => {
  assert.deepEqual(parseOccSymbol('APH261002P00130000'),
    { underlying: 'APH', expiry: '2026-10-02', optionType: 'put', strike: 130 })
  assert.deepEqual(parseOccSymbol('spy261218c00612500'),
    { underlying: 'SPY', expiry: '2026-12-18', optionType: 'call', strike: 612.5 })
  for (const s of ['AAPL', 'BRK.B', '', null, 'APH261002X00130000', 'TOOLONGROOT261002P00130000']) {
    assert.equal(parseOccSymbol(s), null, String(s))
  }
})

test('isOptionRow trusts asset_class first, then the OCC shape', () => {
  assert.equal(isOptionRow(SHORT_PUT), true)
  assert.equal(isOptionRow(STOCK), false)
  assert.equal(isOptionRow(OPTION_TRADE), true)
  assert.equal(isOptionRow(STOCK_TRADE), false)
  assert.equal(isOptionRow({ symbol: 'APH261002P00130000', asset_class: 'us_equity' }), false)
})

test('contractMultiplier: 100 for options unless the row says otherwise, 1 for stock', () => {
  assert.equal(contractMultiplier(SHORT_PUT), 100)
  assert.equal(contractMultiplier({ ...SHORT_PUT, multiplier: 10 }), 10)
  assert.equal(contractMultiplier({ ...SHORT_PUT, multiplier: null }), OPTION_MULTIPLIER)
  assert.equal(contractMultiplier(STOCK), 1)
})

test('isShortPosition reads side, then the sign of qty', () => {
  assert.equal(isShortPosition(SHORT_PUT), true)
  assert.equal(isShortPosition({ qty: -2 }), true)
  assert.equal(isShortPosition(STOCK), false)
  assert.equal(isShortPosition({ side: 'long', qty: -1 }), false)
})

test('labels and quantities: Contracts, unsigned whole numbers for options', () => {
  assert.equal(quantityLabel(SHORT_PUT), 'Contracts')
  assert.equal(quantityLabel(STOCK), 'Shares')
  assert.equal(displayQuantity(SHORT_PUT), '1')
  assert.equal(displayQuantity(STOCK), '12')
  assert.equal(displayQuantity({ qty: null, symbol: 'AAPL' }), '—')
})

test('tradeTotal multiplies option fills by 100 and leaves stock unchanged', () => {
  assert.equal(tradeTotal(OPTION_TRADE), 125)
  assert.equal(tradeTotal(STOCK_TRADE), 2400)
  assert.equal(tradeTotal({ symbol: 'AAPL', qty: null, price: 5 }), 0)
})

test('Close is offered for stock only: close_position reads the equity book', () => {
  assert.equal(canClosePosition(SHORT_PUT), false)
  assert.equal(canClosePosition({ ...SHORT_PUT, side: 'long', qty: 1 }), false)
  assert.equal(canClosePosition(STOCK), true)
})

test('historicalsSymbols never asks /symbol-historicals for an OCC contract', () => {
  assert.deepEqual(historicalsSymbols([SHORT_PUT, STOCK, { symbol: '' }, null]), ['AAPL'])
  assert.deepEqual(historicalsSymbols(undefined), [])
})

test('describeOption uses the position fields, falling back to the symbol', () => {
  assert.equal(describeOption(SHORT_PUT), 'APH $130 Put · 2026-10-02')
  assert.equal(describeOption(OPTION_TRADE), 'APH $130 Put · 2026-10-02')
  assert.equal(describeOption({ symbol: 'SPY261218C00612500' }), 'SPY $612.50 Call · 2026-12-18')
  assert.equal(describeOption(STOCK), '')
})

test('a short put with no quote yields no numbers to invent', () => {
  // The view renders these through fmtMoney / fmtPct, which print an em dash
  // for null. None of the helpers may turn a missing quote into 0.
  assert.equal(SHORT_PUT.last_price, null)
  assert.equal(displayQuantity(SHORT_PUT), '1')
  assert.equal(describeOption(SHORT_PUT), 'APH $130 Put · 2026-10-02')
  assert.equal(canClosePosition(SHORT_PUT), false)
})
```

- [ ] **Step 2: Run it and watch it fail.**

Run: `cd frontend && npm test`
Expected: `optionPositions.test.mjs` fails with `ERR_MODULE_NOT_FOUND ... src/utils/optionPositions.js`.

- [ ] **Step 3: Implement** `frontend/src/utils/optionPositions.js`:

```js
/**
 * Option-aware display rules for the Live Trading terminal.
 *
 * Positions carry asset_class, side, multiplier, underlying, strike and expiry
 * from live_broker_fetch (spec 2026-09-24 section 6.1). recent_trades rows do
 * not, so a row without asset_class falls back to the OCC symbol shape. That
 * is a display fallback only; the engine identifies contracts by Alpaca's
 * contract fields (spec section 9, fix 10).
 *
 * Money the broker reports (market_value, unrealized_pnl) is already in
 * dollars, contract multiplier included. Nothing here multiplies it. Only
 * client-side qty x price totals are multiplied.
 */

export const OPTION_MULTIPLIER = 100

// Root (1-6), YYMMDD, C|P, strike x 1000 in 8 digits.
const OCC_SYMBOL_RE = /^([A-Z][A-Z0-9]{0,5})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/

export function parseOccSymbol(symbol) {
  const m = OCC_SYMBOL_RE.exec(String(symbol ?? '').trim().toUpperCase())
  if (!m) return null
  return {
    underlying: m[1],
    expiry: `20${m[2]}-${m[3]}-${m[4]}`,
    optionType: m[5] === 'P' ? 'put' : 'call',
    strike: Number(m[6]) / 1000,
  }
}

export function isOptionRow(row) {
  const assetClass = String(row?.asset_class ?? '').trim().toLowerCase()
  if (assetClass) return assetClass === 'us_option'
  return parseOccSymbol(row?.symbol) !== null
}

export function contractMultiplier(row) {
  if (!isOptionRow(row)) return 1
  const m = Number(row?.multiplier)
  return row?.multiplier != null && Number.isFinite(m) && m > 0 ? m : OPTION_MULTIPLIER
}

export function isShortPosition(row) {
  const side = String(row?.side ?? '').trim().toLowerCase()
  if (side) return side === 'short'
  return Number(row?.qty) < 0
}

export function quantityLabel(row) {
  return isOptionRow(row) ? 'Contracts' : 'Shares'
}

/** Options: unsigned whole contracts (the SHORT badge carries the sign). */
export function displayQuantity(row, maxFractionDigits = 8) {
  if (row?.qty == null || row.qty === '') return '—'
  const n = Number(row.qty)
  if (!Number.isFinite(n)) return '—'
  if (isOptionRow(row)) return String(Math.abs(Math.trunc(n)))
  return n.toLocaleString(undefined, { maximumFractionDigits: maxFractionDigits })
}

/** Fill total. Identical to the old qty x price for stock; x100 for options. */
export function tradeTotal(trade) {
  return (Number(trade?.qty) || 0) * (Number(trade?.price) || 0) * contractMultiplier(trade)
}

/**
 * The close_position command reads the adapter's equity book only, so a
 * Close on any option would fail with "no open long position". The wheel
 * lane manages its own buy-backs; the operator's lever is Halt.
 */
export function canClosePosition(row) {
  return !isOptionRow(row)
}

/** Symbols worth a /symbol-historicals call: stock only. */
export function historicalsSymbols(positions) {
  return (Array.isArray(positions) ? positions : [])
    .filter(p => p?.symbol && !isOptionRow(p))
    .map(p => p.symbol)
}

function fmtStrike(strike) {
  return Number.isInteger(strike) ? `$${strike}` : `$${strike.toFixed(2)}`
}

/** "APH $130 Put · 2026-10-02" for an option row, '' otherwise. */
export function describeOption(row) {
  if (!isOptionRow(row)) return ''
  const occ = parseOccSymbol(row?.symbol) || {}
  const underlying = String(row?.underlying || occ.underlying || '')
  const rawStrike = row?.strike != null && Number.isFinite(Number(row.strike)) ? Number(row.strike) : occ.strike
  const type = String(row?.option_type || occ.optionType || '').toLowerCase()
  const expiry = String(row?.expiry || occ.expiry || '')
  const parts = []
  if (underlying) parts.push(underlying)
  if (rawStrike != null && Number.isFinite(rawStrike)) parts.push(fmtStrike(rawStrike))
  if (type) parts.push(type === 'put' ? 'Put' : 'Call')
  return `${parts.join(' ')}${expiry ? ` · ${expiry}` : ''}`
}
```

- [ ] **Step 4: Run the tests.**

Run: `cd frontend && npm test`
Expected: `ℹ pass 37`, `ℹ fail 0`.

- [ ] **Step 5: Edit** `frontend/src/views/LiveTradingView.vue`. Every `old` block below occurs exactly once.

5a. The import. Replace
```js
import { fullscreenMode } from '../composables/useFullscreen.js'
```
with
```js
import { fullscreenMode } from '../composables/useFullscreen.js'
import {
  canClosePosition,
  describeOption,
  displayQuantity,
  historicalsSymbols,
  isOptionRow,
  isShortPosition,
  quantityLabel,
  tradeTotal,
} from '../utils/optionPositions.js'
```

5b. No price-history fetch for OCC symbols (spec §10). Replace
```js
const positionSymbols = computed(() => positions.value.map(p => p.symbol).filter(Boolean))
```
with
```js
// Stock only: /symbol-historicals has nothing for an OCC contract symbol.
const positionSymbols = computed(() => historicalsSymbols(positions.value))
```

5c. Recent Executions: the option badge. Replace
```html
                      >{{ t.side || '—' }}</span>
                      <span class="text-base font-black text-slate-100 tracking-wide">{{ t.symbol || '' }}</span>
                    </div>
```
with
```html
                      >{{ t.side || '—' }}</span>
                      <span class="text-base font-black text-slate-100 tracking-wide">{{ t.symbol || '' }}</span>
                      <span
                        v-if="isOptionRow(t)"
                        class="px-1.5 py-0.5 rounded text-[9px] font-bold border uppercase tracking-wider text-violet-300 bg-violet-500/10 border-violet-500/20"
                      >Option</span>
                    </div>
                    <div v-if="isOptionRow(t)" class="text-[10px] text-slate-500 mt-0.5">{{ describeOption(t) }}</div>
```

5d. Recent Executions: Contracts instead of Shares (line 1187). Replace
```html
                    <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Shares</div>
                    <div
                      class="text-sm font-bold text-slate-100 tabular-nums"
                      :title="String(t.qty || 0)"
                    >{{ Number(t.qty || 0).toLocaleString(undefined, { maximumFractionDigits: 4 }) }}</div>
```
with
```html
                    <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider">{{ quantityLabel(t) }}</div>
                    <div
                      class="text-sm font-bold text-slate-100 tabular-nums"
                      :title="String(t.qty || 0)"
                    >{{ displayQuantity(t, 4) }}</div>
```

5e. Recent Executions: ×100 total (line 1195). Replace
```html
                    <div class="text-sm font-bold text-slate-100 tabular-nums">{{ fmtMoney((Number(t.qty) || 0) * (Number(t.price) || 0)) }}</div>
```
with
```html
                    <div class="text-sm font-bold text-slate-100 tabular-nums">{{ fmtMoney(tradeTotal(t)) }}</div>
```

5f. Active Positions: badges and the contract line. Replace
```html
                      <span class="text-base font-black text-slate-100 tracking-wide">{{ p.symbol }}</span>
                      <span
                        class="text-xs font-bold tabular-nums"
                        :class="p.avg_entry_price ? pnlColorClass(p.unrealized_pnl_pct) : 'text-slate-500'"
                      >{{ p.avg_entry_price ? fmtPct(p.unrealized_pnl_pct) : '—' }}</span>
                    </div>
```
with
```html
                      <span class="text-base font-black text-slate-100 tracking-wide">{{ p.symbol }}</span>
                      <span
                        v-if="isOptionRow(p)"
                        class="px-1.5 py-0.5 rounded text-[9px] font-bold border uppercase tracking-wider text-violet-300 bg-violet-500/10 border-violet-500/20"
                      >Option</span>
                      <span
                        v-if="isOptionRow(p) && isShortPosition(p)"
                        class="px-1.5 py-0.5 rounded text-[9px] font-bold border uppercase tracking-wider text-amber-300 bg-amber-500/10 border-amber-500/20"
                      >Short</span>
                      <span
                        class="text-xs font-bold tabular-nums"
                        :class="p.avg_entry_price ? pnlColorClass(p.unrealized_pnl_pct) : 'text-slate-500'"
                      >{{ p.avg_entry_price ? fmtPct(p.unrealized_pnl_pct) : '—' }}</span>
                    </div>
                    <div v-if="isOptionRow(p)" class="text-[10px] text-slate-500 mt-0.5">{{ describeOption(p) }}</div>
```

5g. Active Positions: the chart placeholder (line 1252). Replace
```html
                    {{ positionHistLoading ? 'Loading chart…' : 'No price history' }}
```
with
```html
                    {{ isOptionRow(p) ? 'No price chart for options' : (positionHistLoading ? 'Loading chart…' : 'No price history') }}
```

5h. Active Positions: Contracts instead of Shares (line 1265). Replace
```html
                    <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Shares</div>
                    <div
                      class="text-xs font-bold text-slate-300 tabular-nums"
                      :title="String(p.qty || 0)"
                    >{{ Number(p.qty || 0).toLocaleString(undefined, { maximumFractionDigits: 8 }) }}</div>
```
with
```html
                    <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider">{{ quantityLabel(p) }}</div>
                    <div
                      class="text-xs font-bold text-slate-300 tabular-nums"
                      :title="String(p.qty || 0)"
                    >{{ displayQuantity(p, 8) }}</div>
```

5i. Active Positions: hide Close for options, part 1. Replace
```html
                <div class="flex justify-end">
                  <button
                    @click="openClosePosModal(p.symbol)"
```
with
```html
                <div v-if="canClosePosition(p)" class="flex justify-end">
                  <button
                    @click="openClosePosModal(p.symbol)"
```

5j. Part 2. Replace
```html
                    <span class="material-symbols-outlined text-[13px]">logout</span>
                    Close
                  </button>
                </div>
```
with
```html
                    <span class="material-symbols-outlined text-[13px]">logout</span>
                    Close
                  </button>
                </div>
                <p v-else class="text-[10px] text-slate-600 text-right">
                  Managed by the wheel lane. Use Halt to stop it.
                </p>
```

The "Last", "Market Value" and "P&L $" cells need no edit: `fmtMoney(null)` and `fmtPct(null)` already print `—` (LiveTradingView 754-765). Do not touch them.

- [ ] **Step 6: Run the gate.**

Run: `cd frontend && npm test && npm run build`
Expected: `ℹ pass 37`, then `✓ built in`.

- [ ] **Step 7: Manual check.** It needs plan A-live's fields; on today's API only the equity items apply.
  1. On an equity-only account (alpaca-main, read-only viewing), Live Trading looks exactly as before: "Shares", Close buttons and charts.
  2. On `swing-paper` holding a short put:
     - OPTION and SHORT badges show, with a line like "APH $130 Put · 2026-10-02";
     - "Contracts" reads 1; the Close button is replaced by "Managed by the wheel lane. Use Halt to stop it.";
     - "No price chart for options" shows;
     - the Network tab has no `symbol-historicals` request containing the OCC symbol.
  3. A sell-to-open fill in Recent Executions shows OPTION, "Contracts", and Total = premium × 100.
  4. A put with no quote shows `—` for Last, Market Value and P&L, never `$0.00`.

- [ ] **Step 8: Commit.** Run `mcp__gitnexus__detect_changes()` first. Expected: `optionPositions.js` is new; in `LiveTradingView.vue` only `positionSymbols` and template lines change.

```bash
git add frontend/src/utils/optionPositions.js frontend/tests/optionPositions.test.mjs frontend/src/views/LiveTradingView.vue
git commit -F - <<'EOF'
feat(web): show option positions and fills correctly in Live Trading

Option rows get an Option badge (and Short for short positions), a contract
description, Contracts instead of Shares, and fill totals times the contract
multiplier. Close is hidden for options because close_position only reads the
equity book, and no price-history request is made for OCC symbols. Broker
money fields are never multiplied; a missing quote still renders as a dash.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 5: Mobile swing repository and approval controller

**Files:**
- Create: `mobile/lib/features/swing/data/swing_repository.dart`
- Create: `mobile/lib/features/swing/application/swing_controller.dart`
- Create: `mobile/test/features/swing/swing_fakes.dart` (not a `_test.dart` file; the runner never runs it alone)
- Test: `mobile/test/features/swing/swing_repository_test.dart`
- Test: `mobile/test/features/swing/swing_controller_test.dart`

**Interfaces:**
- Consumes:
  - `ApiClient.get<T>(path, {query})` and `ApiClient.post<T>(path, {body})`, plus `apiClientProvider` (`core/network/api_client.dart`);
  - `ApiError.message` and `ApiError.statusCode` (`core/network/api_error.dart`);
  - `IntervalPoller` and `appLifecycleProvider` (`core/polling/poller.dart`).
- Produces (used by Task 6):
  - `swing_repository.dart`:
    - models `SwingSignal`, `WheelPut`, `WheelScan`, `WheelSnapshot` (+ `WheelSnapshot.empty`);
    - `class SwingRepository { Future<List<SwingSignal>> pendingSignals(String instanceId); Future<void> decide(String instanceId, String signalId, String decision, {String? reason}); Future<WheelSnapshot> wheel(String instanceId); }`;
    - `swingRepositoryProvider`.
  - `SwingSignal` getters: `isWheel`, `allowsHalf`, `keyRisksText`, `entry`, `stop`, `target`, `shares`, `contract`, `strike`, `expiry`, `qty`, `limitPrice`, `premiumEst`, `creditEst`, `collateral`.
  - `WheelPut` getter: `monitorWillBuyBack`.
  - `swing_controller.dart`:
    - `SwingLanes {swing, wheel, any}` and `swingLanesOf(Map<String, dynamic>? strategyDoc)`;
    - `decisionLabel(String)`, `decisionConfirmBody(SwingSignal, String)`, `decisionSuccessMessage(SwingSignal, String)`;
    - `enum DecisionOutcome {recorded, noLongerPending, failed, ignored}` and `DecisionResult(outcome, message)`;
    - `PendingSignalsState {signals, deciding, refreshError, isDeciding(id)}`;
    - `pendingSignalsProvider` (autoDispose family by instance id) whose notifier has `refresh()` and `decide(SwingSignal, String decision, {String? reason}) -> Future<DecisionResult>`;
    - `wheelSnapshotProvider` (autoDispose family by instance id).
  - `swing_fakes.dart`: `FakeSwingRepo(pending, {wheelSnapshot})` with `decideCalls`, `decideError`, `listError`, `wheelError` and `gate`; plus fixtures `swingSignal(id, {symbol, createdAt, reasoning})` and `wheelSignal(id)`.

These follow `learning_repository.dart` (models with nullable-tolerant `fromJson`, and a `const` repository over `ApiClient`, 298/346) and `account_positions_controller.dart` (a polled family notifier that pauses in the background).

- [ ] **Step 0: Impact.** These are new files only, so there is nothing to run impact on. Existing symbols are consumed, not changed.

- [ ] **Step 1: Write the shared fakes** `mobile/test/features/swing/swing_fakes.dart`:

```dart
import 'dart:async';

import 'package:intellistock_mobile/features/swing/data/swing_repository.dart';

/// Test double for [SwingRepository]. Not a *_test.dart file, so the runner
/// does not execute it on its own.
class FakeSwingRepo implements SwingRepository {
  FakeSwingRepo(this.pending, {this.wheelSnapshot = WheelSnapshot.empty});

  List<SwingSignal> pending;
  WheelSnapshot wheelSnapshot;
  final decideCalls = <String>[];
  Object? decideError;
  Object? listError;
  Object? wheelError;

  /// When set, decide() waits on it: lets a test hold a request in flight.
  Completer<void>? gate;

  @override
  Future<List<SwingSignal>> pendingSignals(String instanceId) async {
    if (listError != null) throw listError!;
    return List.of(pending);
  }

  @override
  Future<void> decide(String instanceId, String signalId, String decision,
      {String? reason}) async {
    decideCalls.add('$signalId:$decision');
    if (gate != null) await gate!.future;
    if (decideError != null) throw decideError!;
  }

  @override
  Future<WheelSnapshot> wheel(String instanceId) async {
    if (wheelError != null) throw wheelError!;
    return wheelSnapshot;
  }
}

SwingSignal swingSignal(
  String id, {
  String symbol = 'AAPL',
  String createdAt = '2026-09-24T13:15:00Z',
  String reasoning = 'Pullback to the 50-day in an uptrend.',
}) =>
    SwingSignal.fromJson({
      'id': id,
      'lane': 'swing',
      'symbol': symbol,
      'session': '2026-09-24',
      'created_at': createdAt,
      'score': 62,
      'recommendation': 'REVIEW',
      'reasoning': reasoning,
      'key_risks': ['earnings in 9 days'],
      'proposal': {'entry': 200.0, 'stop': 188.0, 'target': 218.0, 'shares': 6},
      'status': 'pending',
    });

SwingSignal wheelSignal(String id) => SwingSignal.fromJson({
      'id': id,
      'lane': 'wheel',
      'symbol': 'APH',
      'session': '2026-09-21',
      'created_at': '2026-09-21T14:30:00Z',
      'score': 55,
      'recommendation': 'REVIEW',
      'reasoning': 'IV rank is high.',
      'key_risks': <String>[],
      'proposal': {
        'contract': 'APH261002P00130000',
        'strike': 130,
        'expiry': '2026-10-02',
        'qty': 1,
        'limit_price': 1.23,
        'premium_est': 1.3,
      },
      'status': 'pending',
    });
```

- [ ] **Step 2: Write the failing repository test** `mobile/test/features/swing/swing_repository_test.dart`:

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/network/api_client.dart';
import 'package:intellistock_mobile/features/swing/data/swing_repository.dart';

class _FakeApiClient implements ApiClient {
  final calls = <Map<String, dynamic>>[];
  Object? getResponse;

  @override
  Future<T> get<T>(String path, {Map<String, dynamic>? query}) async {
    calls.add({'method': 'GET', 'path': path, 'query': query});
    return getResponse as T;
  }

  @override
  Future<T> post<T>(String path, {Object? body, Map<String, dynamic>? query}) async {
    calls.add({'method': 'POST', 'path': path, 'body': body});
    return null as T;
  }

  @override
  Future<T> put<T>(String path, {Object? body}) async => null as T;
  @override
  Future<T> patch<T>(String path, {Object? body}) async => null as T;
  @override
  Future<T> delete<T>(String path, {Map<String, dynamic>? query}) async =>
      null as T;
}

Map<String, dynamic> swingJson({String id = 'a1', String status = 'pending', String createdAt = '2026-09-24T13:15:02Z'}) => {
      'id': id,
      'instance_id': 'swing-paper',
      'lane': 'swing',
      'symbol': 'AAPL',
      'session': '2026-09-24',
      'created_at': createdAt,
      'score': 62,
      'recommendation': 'REVIEW',
      'reasoning': 'Pullback to the 50-day in an uptrend.',
      'key_risks': ['earnings in 9 days', '', '  sector rotation  '],
      'size_adjustment': 1.0,
      'proposal': {'entry': 200.0, 'stop': 188.0, 'target': 218.0, 'shares': 6},
      'status': status,
    };

Map<String, dynamic> wheelJson() => {
      'id': 'w1',
      'lane': 'wheel',
      'symbol': 'APH',
      'session': '2026-09-21',
      'created_at': '2026-09-21T14:30:00Z',
      'score': 55,
      'recommendation': 'REVIEW',
      'reasoning': 'IV rank is high.',
      'key_risks': <String>[],
      'size_adjustment': 1.0,
      'proposal': {
        'contract': 'APH261002P00130000',
        'strike': 130,
        'expiry': '2026-10-02',
        'qty': 1,
        'limit_price': 1.23,
        'premium_est': 1.3,
        'delta': -0.24,
      },
      'status': 'pending',
    };

void main() {
  group('SwingSignal.fromJson', () {
    test('swing proposal fields and risks', () {
      final s = SwingSignal.fromJson(swingJson());
      expect(s.isWheel, isFalse);
      expect(s.allowsHalf, isTrue);
      expect(s.entry, 200.0);
      expect(s.stop, 188.0);
      expect(s.target, 218.0);
      expect(s.shares, 6);
      expect(s.keyRisksText, 'earnings in 9 days · sector rotation');
    });

    test('wheel proposal fields, credit and collateral', () {
      final s = SwingSignal.fromJson(wheelJson());
      expect(s.isWheel, isTrue);
      expect(s.allowsHalf, isFalse);
      expect(s.contract, 'APH261002P00130000');
      expect(s.strike, 130.0);
      expect(s.qty, 1);
      expect(s.limitPrice, 1.23);
      expect(s.creditEst, closeTo(130.0, 1e-9));
      expect(s.collateral, 13000.0);
    });

    test('missing fields do not throw', () {
      final s = SwingSignal.fromJson(const {'id': 'x'});
      expect(s.lane, 'swing');
      expect(s.status, 'pending');
      expect(s.score, isNull);
      expect(s.entry, isNull);
      expect(s.creditEst, isNull);
      expect(s.keyRisksText, '');
    });
  });

  group('SwingRepository', () {
    test('pendingSignals GETs with status=pending, keeps pending only, newest first', () async {
      final api = _FakeApiClient()
        ..getResponse = {
          'signals': [
            swingJson(id: 'old', createdAt: '2026-09-23T13:15:00Z'),
            swingJson(id: 'done', status: 'approved'),
            swingJson(id: 'new', createdAt: '2026-09-24T13:15:00Z'),
            'junk',
          ],
        };
      final rows = await SwingRepository(api).pendingSignals('swing-paper');
      expect(rows.map((s) => s.id), ['new', 'old']);
      expect(api.calls.single['path'], '/instances/swing-paper/swing/signals');
      expect(api.calls.single['query'], {'status': 'pending'});
    });

    test('pendingSignals also accepts a bare list', () async {
      final api = _FakeApiClient()..getResponse = [wheelJson()];
      final rows = await SwingRepository(api).pendingSignals('i1');
      expect(rows.single.id, 'w1');
    });

    test('decide POSTs the decision, and the reason only when given', () async {
      final api = _FakeApiClient();
      final repo = SwingRepository(api);
      await repo.decide('i1', 'a1', 'approve_half');
      await repo.decide('i1', 'a1', 'reject', reason: '  too close to earnings ');
      await repo.decide('i1', 'a1', 'reject', reason: '   ');
      expect(api.calls[0]['path'], '/instances/i1/swing/signals/a1/decision');
      expect(api.calls[0]['body'], {'decision': 'approve_half'});
      expect(api.calls[1]['body'],
          {'decision': 'reject', 'reason': 'too close to earnings'});
      expect(api.calls[2]['body'], {'decision': 'reject'});
    });

    test('wheel parses the addendum shape and tolerates nulls', () async {
      final api = _FakeApiClient()
        ..getResponse = {
          'open_puts': [
            {
              'contract': 'APH261002P00130000',
              'underlying': 'APH',
              'strike': 130,
              'expiry': '2026-10-02',
              'qty': 1,
              'avg_entry_price': 1.23,
              'current_price': null,
              'underlying_price': 127.4,
              'itm_pct': 2.0,
              'dte': 8,
              'collateral': 13000,
              'unrealized_pl': null,
            },
          ],
          'collateral_total': 13000,
          'cash': 25000,
          'recent_scans': [
            {'id': 's1', 'session': '2026-09-21', 'symbol': 'APH', 'strike': 130,
             'expiry': '2026-10-02', 'score': 55, 'status': 'pending', 'skip_reason': null},
          ],
        };
      final w = await SwingRepository(api).wheel('i1');
      expect(api.calls.single['path'], '/instances/i1/wheel');
      expect(w.openPuts.single.currentPrice, isNull);
      expect(w.openPuts.single.monitorWillBuyBack, isFalse);
      expect(w.collateralTotal, 13000.0);
      expect(w.recentScans.single.skipReason, '');
    });
  });

  test('WheelPut.monitorWillBuyBack mirrors the 15:45 monitor rules', () {
    WheelPut put(double? itm, int? dte) => WheelPut(
        contract: 'c', underlying: 'u', expiry: '', itmPct: itm, dte: dte);
    expect(put(10, 20).monitorWillBuyBack, isTrue);
    expect(put(5, 2).monitorWillBuyBack, isTrue);
    expect(put(0.4, 0).monitorWillBuyBack, isTrue);
    expect(put(5, 3).monitorWillBuyBack, isFalse);
    expect(put(-3, 0).monitorWillBuyBack, isFalse);
    expect(put(null, 0).monitorWillBuyBack, isFalse);
  });
}
```

- [ ] **Step 3: Write the failing controller test** `mobile/test/features/swing/swing_controller_test.dart`:

```dart
import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/network/api_error.dart';
import 'package:intellistock_mobile/features/swing/application/swing_controller.dart';
import 'package:intellistock_mobile/features/swing/data/swing_repository.dart';

import 'swing_fakes.dart';

SwingSignal signal(String id) =>
    swingSignal(id, createdAt: '2026-09-24T13:15:0${id.length}Z');

Future<ProviderContainer> start(FakeSwingRepo repo) async {
  final container = ProviderContainer(
    overrides: [swingRepositoryProvider.overrideWithValue(repo)],
  );
  addTearDown(container.dispose);
  container.listen(pendingSignalsProvider('i1'), (_, _) {});
  await container.read(pendingSignalsProvider('i1').future);
  return container;
}

PendingSignalsState read(ProviderContainer c) =>
    c.read(pendingSignalsProvider('i1')).requireValue;

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('swingLanesOf', () {
    test('finds lanes by id or class name, ignores everything else', () {
      expect(swingLanesOf({'strategies': [{'strategy': 'strategy_swing'}]}).swing, isTrue);
      final l = swingLanesOf({
        'strategies': [
          {'strategy': 'StrategyWheel'},
          {'strategy': 'strategy_eb'},
          'junk',
        ],
      });
      expect([l.swing, l.wheel, l.any], [false, true, true]);
      expect(swingLanesOf({'strategies': [{'strategy': 'strategy_eb'}]}).any, isFalse);
      expect(swingLanesOf(null).any, isFalse);
      expect(swingLanesOf({'strategies': 'oops'}).any, isFalse);
    });
  });

  group('PendingSignalsNotifier.decide', () {
    test('a double tap sends one request', () async {
      final repo = FakeSwingRepo([signal('a1')])..gate = Completer<void>();
      final c = await start(repo);
      final notifier = c.read(pendingSignalsProvider('i1').notifier);

      final first = notifier.decide(signal('a1'), 'approve');
      expect(read(c).isDeciding('a1'), isTrue);
      final second = await notifier.decide(signal('a1'), 'approve');
      expect(second.outcome, DecisionOutcome.ignored);

      repo.gate!.complete();
      expect((await first).outcome, DecisionOutcome.recorded);
      expect(repo.decideCalls, ['a1:approve']);
      expect(read(c).signals, isEmpty);
      expect(read(c).deciding, isEmpty);
    });

    test('decided elsewhere (400): card removed, server reason shown', () async {
      final repo = FakeSwingRepo([signal('a1'), signal('b22')])
        ..decideError = ApiError('signal a1 is approved, not pending', statusCode: 400);
      final c = await start(repo);
      final result = await c
          .read(pendingSignalsProvider('i1').notifier)
          .decide(signal('a1'), 'approve');
      expect(result.outcome, DecisionOutcome.noLongerPending);
      expect(result.message, 'signal a1 is approved, not pending');
      expect(read(c).signals.map((s) => s.id), ['b22']);
      expect(read(c).deciding, isEmpty);
    });

    test('forbidden (403): card stays, buttons re-enable, reason shown', () async {
      final repo = FakeSwingRepo([signal('a1')])
        ..decideError = ApiError('not your instance', statusCode: 403);
      final c = await start(repo);
      final result = await c
          .read(pendingSignalsProvider('i1').notifier)
          .decide(signal('a1'), 'reject');
      expect(result.outcome, DecisionOutcome.failed);
      expect(result.message, 'not your instance');
      expect(read(c).signals.map((s) => s.id), ['a1']);
      expect(read(c).isDeciding('a1'), isFalse);
    });

    test('expired session (401): card stays, message says so', () async {
      final repo = FakeSwingRepo([signal('a1')])
        ..decideError = ApiError('Not authenticated', statusCode: 401);
      final c = await start(repo);
      final result = await c
          .read(pendingSignalsProvider('i1').notifier)
          .decide(signal('a1'), 'approve');
      expect(result.outcome, DecisionOutcome.failed);
      expect(result.message, 'Session expired — please sign in again.');
      expect(read(c).signals, hasLength(1));
    });

    test('a poll that raced a recorded decision does not resurrect the card', () async {
      final repo = FakeSwingRepo([signal('a1')]);
      final c = await start(repo);
      final notifier = c.read(pendingSignalsProvider('i1').notifier);
      await notifier.decide(signal('a1'), 'approve');
      await notifier.refresh(); // the server still lists a1 (stale read)
      expect(read(c).signals, isEmpty);
    });

    test('a failed poll keeps the last good list and reports the error', () async {
      final repo = FakeSwingRepo([signal('a1')]);
      final c = await start(repo);
      repo.listError = ApiError('Cannot reach the server.');
      await c.read(pendingSignalsProvider('i1').notifier).refresh();
      expect(read(c).signals.map((s) => s.id), ['a1']);
      expect(read(c).refreshError, 'Cannot reach the server.');
    });
  });

  test('copy names the symbol and the decision', () {
    final s = signal('a1');
    expect(decisionLabel('approve_half'), 'Approve ½');
    expect(decisionConfirmBody(s, 'reject'), contains('final'));
    expect(decisionSuccessMessage(s, 'approve'), startsWith('Approved AAPL'));
  });
}
```

- [ ] **Step 4: Run them and watch them fail.**

Run: `cd mobile && flutter test test/features/swing`
Expected: FAIL at load: `Error: Error when reading 'lib/features/swing/data/swing_repository.dart': No such file or directory`.

- [ ] **Step 5: Implement** `mobile/lib/features/swing/data/swing_repository.dart`:

```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';

// Shapes: SwingSignals is section 5 of
// docs/superpowers/plans/2026-09-24-swing-port-interfaces.md; the wheel
// payload is the Contract addendum of
// docs/superpowers/plans/2026-09-24-swing-port-C-ui.md. If plan B ships a
// different wheel shape, WheelSnapshot.fromJson is the only reader to change.

double? _num(Object? v) =>
    v is num ? v.toDouble() : (v is String ? double.tryParse(v) : null);

int? _int(Object? v) =>
    v is num ? v.toInt() : (v is String ? int.tryParse(v) : null);

String _str(Object? v) => v == null ? '' : v.toString();

// ── Models ────────────────────────────────────────────────────────────────────

/// One AI-scored candidate awaiting (or past) an operator decision.
class SwingSignal {
  const SwingSignal({
    required this.id,
    required this.lane,
    required this.symbol,
    required this.session,
    required this.createdAt,
    required this.score,
    required this.recommendation,
    required this.reasoning,
    required this.keyRisks,
    required this.sizeAdjustment,
    required this.proposal,
    required this.status,
  });

  final String id;

  /// "swing" | "wheel".
  final String lane;
  final String symbol;

  /// NY trading date, YYYY-MM-DD.
  final String session;
  final String createdAt;
  final int? score;
  final String recommendation;
  final String reasoning;
  final List<String> keyRisks;
  final double? sizeAdjustment;
  final Map<String, dynamic> proposal;
  final String status;

  bool get isWheel => lane == 'wheel';

  /// Approve-half exists for swing entries only; the wheel sizes in whole
  /// contracts.
  bool get allowsHalf => lane == 'swing';

  String get keyRisksText => keyRisks
      .map((r) => r.trim())
      .where((r) => r.isNotEmpty)
      .join(' · ');

  // swing proposal
  double? get entry => _num(proposal['entry']);
  double? get stop => _num(proposal['stop']);
  double? get target => _num(proposal['target']);
  int? get shares => _int(proposal['shares']);

  // wheel proposal
  String get contract => _str(proposal['contract']);
  double? get strike => _num(proposal['strike']);
  String get expiry => _str(proposal['expiry']);
  int? get qty => _int(proposal['qty']);
  double? get limitPrice => _num(proposal['limit_price']);
  double? get premiumEst => _num(proposal['premium_est']);

  /// Premium for the whole order: per-share premium x 100 x contracts.
  double? get creditEst {
    final p = premiumEst;
    final q = qty;
    return (p == null || q == null) ? null : p * 100 * q;
  }

  /// Cash a sold put ties up: strike x 100 x contracts.
  double? get collateral {
    final s = strike;
    final q = qty;
    return (s == null || q == null) ? null : s * 100 * q;
  }

  factory SwingSignal.fromJson(Map<String, dynamic> j) => SwingSignal(
        id: _str(j['id']),
        lane: _str(j['lane']).isEmpty ? 'swing' : _str(j['lane']),
        symbol: _str(j['symbol']),
        session: _str(j['session']),
        createdAt: _str(j['created_at']),
        score: _int(j['score']),
        recommendation: _str(j['recommendation']),
        reasoning: _str(j['reasoning']),
        keyRisks: ((j['key_risks'] as List?) ?? const [])
            .map((e) => e.toString())
            .toList(),
        sizeAdjustment: _num(j['size_adjustment']),
        proposal: (j['proposal'] as Map?)?.cast<String, dynamic>() ?? const {},
        status: _str(j['status']).isEmpty ? 'pending' : _str(j['status']),
      );
}

/// One open cash-secured put.
class WheelPut {
  const WheelPut({
    required this.contract,
    required this.underlying,
    this.strike,
    required this.expiry,
    this.qty,
    this.avgEntryPrice,
    this.currentPrice,
    this.underlyingPrice,
    this.itmPct,
    this.dte,
    this.collateral,
    this.unrealizedPl,
  });

  final String contract;
  final String underlying;
  final double? strike;
  final String expiry;
  final int? qty;
  final double? avgEntryPrice;
  final double? currentPrice;
  final double? underlyingPrice;

  /// Percent the underlying sits below the strike; > 0 means in the money.
  final double? itmPct;
  final int? dte;
  final double? collateral;
  final double? unrealizedPl;

  /// Exactly the puts the 15:45 ET monitor buys back (spec section 5.2).
  bool get monitorWillBuyBack {
    final itm = itmPct;
    if (itm == null) return false;
    final d = dte;
    return itm >= 10 ||
        (itm >= 5 && d != null && d <= 2) ||
        (itm > 0 && d == 0);
  }

  factory WheelPut.fromJson(Map<String, dynamic> j) => WheelPut(
        contract: _str(j['contract']),
        underlying: _str(j['underlying']),
        strike: _num(j['strike']),
        expiry: _str(j['expiry']),
        qty: _int(j['qty']),
        avgEntryPrice: _num(j['avg_entry_price']),
        currentPrice: _num(j['current_price']),
        underlyingPrice: _num(j['underlying_price']),
        itmPct: _num(j['itm_pct']),
        dte: _int(j['dte']),
        collateral: _num(j['collateral']),
        unrealizedPl: _num(j['unrealized_pl']),
      );
}

/// One row of the weekly wheel scan log (SwingWheelScans).
class WheelScan {
  const WheelScan({
    required this.id,
    required this.session,
    required this.symbol,
    this.strike,
    required this.expiry,
    this.score,
    required this.status,
    required this.skipReason,
  });

  final String id;
  final String session;
  final String symbol;
  final double? strike;
  final String expiry;
  final int? score;

  /// "placed" | "pending" | "rejected" | "skipped".
  final String status;
  final String skipReason;

  factory WheelScan.fromJson(Map<String, dynamic> j) => WheelScan(
        id: _str(j['id']),
        session: _str(j['session']),
        symbol: _str(j['symbol']),
        strike: _num(j['strike']),
        expiry: _str(j['expiry']),
        score: _int(j['score']),
        status: _str(j['status']),
        skipReason: _str(j['skip_reason']),
      );
}

/// GET /instances/{id}/wheel.
class WheelSnapshot {
  const WheelSnapshot({
    this.openPuts = const [],
    this.collateralTotal,
    this.cash,
    this.recentScans = const [],
  });

  static const empty = WheelSnapshot();

  final List<WheelPut> openPuts;
  final double? collateralTotal;
  final double? cash;
  final List<WheelScan> recentScans;

  factory WheelSnapshot.fromJson(Map<String, dynamic> j) => WheelSnapshot(
        openPuts: ((j['open_puts'] as List?) ?? const [])
            .whereType<Map>()
            .map((m) => WheelPut.fromJson(m.cast<String, dynamic>()))
            .toList(),
        collateralTotal: _num(j['collateral_total']),
        cash: _num(j['cash']),
        recentScans: ((j['recent_scans'] as List?) ?? const [])
            .whereType<Map>()
            .map((m) => WheelScan.fromJson(m.cast<String, dynamic>()))
            .toList(),
      );
}

// ── Repository ────────────────────────────────────────────────────────────────

class SwingRepository {
  const SwingRepository(this._client);

  final ApiClient _client;

  /// Pending signals, newest first. Accepts a bare list or {signals: [...]}
  /// and drops anything not pending, in case an older API build ignores the
  /// status filter.
  Future<List<SwingSignal>> pendingSignals(String instanceId) async {
    final data = await _client.get<dynamic>(
      '/instances/$instanceId/swing/signals',
      query: {'status': 'pending'},
    );
    final rows = data is List
        ? data
        : (data is Map ? (data['signals'] as List?) ?? const [] : const []);
    return rows
        .whereType<Map>()
        .map((m) => SwingSignal.fromJson(m.cast<String, dynamic>()))
        .where((s) => s.id.isNotEmpty && s.status == 'pending')
        .toList()
      ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
  }

  /// POST .../decision with {decision, reason?}. decision is
  /// "approve" | "approve_half" | "reject". Throws ApiError on non-2xx.
  Future<void> decide(
    String instanceId,
    String signalId,
    String decision, {
    String? reason,
  }) async {
    final body = <String, dynamic>{'decision': decision};
    final r = reason?.trim();
    if (r != null && r.isNotEmpty) body['reason'] = r;
    await _client.post<dynamic>(
      '/instances/$instanceId/swing/signals/$signalId/decision',
      body: body,
    );
  }

  Future<WheelSnapshot> wheel(String instanceId) async {
    final data = await _client.get<dynamic>('/instances/$instanceId/wheel');
    return data is Map
        ? WheelSnapshot.fromJson(data.cast<String, dynamic>())
        : WheelSnapshot.empty;
  }
}

final swingRepositoryProvider = Provider<SwingRepository>(
  (ref) => SwingRepository(ref.watch(apiClientProvider)),
);
```

- [ ] **Step 6: Implement** `mobile/lib/features/swing/application/swing_controller.dart`:

```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_error.dart';
import '../../../core/polling/poller.dart';
import '../data/swing_repository.dart';

// ── Which lanes does this instance run? ──────────────────────────────────────

class SwingLanes {
  const SwingLanes({required this.swing, required this.wheel});

  static const none = SwingLanes(swing: false, wheel: false);

  final bool swing;
  final bool wheel;

  bool get any => swing || wheel;
}

String _canonicalStrategyId(Object? raw) => (raw ?? '')
    .toString()
    .trim()
    .replaceAllMapped(RegExp(r'([a-z0-9])([A-Z])'), (m) => '${m[1]}_${m[2]}')
    .toLowerCase();

/// Reads the instance's nested strategy document (`Instance.strategy`).
/// Accepts the lowercase id ("strategy_swing") and the class name
/// ("StrategySwing").
SwingLanes swingLanesOf(Map<String, dynamic>? strategyDoc) {
  final subs = strategyDoc?['strategies'];
  if (subs is! List) return SwingLanes.none;
  var swing = false;
  var wheel = false;
  for (final sub in subs) {
    if (sub is! Map) continue;
    final id = _canonicalStrategyId(sub['strategy']);
    if (id == 'strategy_swing') swing = true;
    if (id == 'strategy_wheel') wheel = true;
  }
  return SwingLanes(swing: swing, wheel: wheel);
}

// ── Copy shared by the confirm dialog and the snackbar ───────────────────────

String decisionLabel(String decision) => switch (decision) {
      'approve' => 'Approve',
      'approve_half' => 'Approve ½',
      _ => 'Reject',
    };

String decisionConfirmBody(SwingSignal s, String decision) => switch (decision) {
      'approve' =>
        'Approve ${s.symbol}? The order is rebuilt at the live price and placed within seconds.',
      'approve_half' =>
        'Approve ${s.symbol} at half size? The order is rebuilt at the live price and placed within seconds.',
      _ => 'Reject ${s.symbol}? Decisions are final.',
    };

String decisionSuccessMessage(SwingSignal s, String decision) =>
    switch (decision) {
      'approve' =>
        "Approved ${s.symbol}. The order goes out on the broker's next command poll.",
      'approve_half' =>
        "Approved ${s.symbol} at half size. The order goes out on the broker's next command poll.",
      _ => 'Rejected ${s.symbol}.',
    };

// ── Pending signals (polled) ─────────────────────────────────────────────────

enum DecisionOutcome {
  /// The server recorded it; the card is gone for good.
  recorded,

  /// 400/404/409: already decided elsewhere (or gone). The card is removed;
  /// the next poll brings it back only if it is in fact still pending.
  noLongerPending,

  /// Anything else (401, 403, 5xx, network). The card stays and can be retried.
  failed,

  /// A second tap while the first request was in flight. Nothing was sent.
  ignored,
}

class DecisionResult {
  const DecisionResult(this.outcome, this.message);
  final DecisionOutcome outcome;
  final String message;
}

class PendingSignalsState {
  const PendingSignalsState({
    this.signals = const [],
    this.deciding = const {},
    this.refreshError,
  });

  final List<SwingSignal> signals;

  /// Signal ids whose decision request is in flight — their buttons disable.
  final Set<String> deciding;

  /// Set when the latest poll failed; [signals] is then the last good list.
  final String? refreshError;

  bool isDeciding(String id) => deciding.contains(id);

  PendingSignalsState copyWith({
    List<SwingSignal>? signals,
    Set<String>? deciding,
    String? refreshError,
    bool clearRefreshError = false,
  }) =>
      PendingSignalsState(
        signals: signals ?? this.signals,
        deciding: deciding ?? this.deciding,
        refreshError:
            clearRefreshError ? null : (refreshError ?? this.refreshError),
      );
}

class PendingSignalsNotifier
    extends AutoDisposeFamilyAsyncNotifier<PendingSignalsState, String> {
  static const pollEvery = Duration(seconds: 30);

  IntervalPoller? _poller;

  /// Ids this device decided. A poll that raced the decision must not bring
  /// the card back.
  final Set<String> _decided = <String>{};

  @override
  Future<PendingSignalsState> build(String arg) async {
    final lifecycle = ref.read(appLifecycleProvider);
    final rows = await ref.read(swingRepositoryProvider).pendingSignals(arg);

    _poller?.dispose();
    _poller = IntervalPoller(fetch: refresh, interval: () => pollEvery);
    if (lifecycle.isForeground) {
      _poller!.start();
    } else {
      _poller!.pause();
    }
    ref.listen(appLifecycleProvider, (_, next) {
      if (next.isForeground) {
        _poller?.resume();
      } else {
        _poller?.pause();
      }
    });
    ref.onDispose(() => _poller?.dispose());
    return PendingSignalsState(signals: _visible(rows));
  }

  List<SwingSignal> _visible(List<SwingSignal> rows) =>
      rows.where((s) => !_decided.contains(s.id)).toList();

  /// One poll cycle. A failure keeps the last good list and says so.
  Future<void> refresh() async {
    try {
      final rows = await ref.read(swingRepositoryProvider).pendingSignals(arg);
      final current = state.valueOrNull ?? const PendingSignalsState();
      state = AsyncData(
          current.copyWith(signals: _visible(rows), clearRefreshError: true));
    } catch (err) {
      final current = state.valueOrNull;
      if (current == null) return;
      state = AsyncData(current.copyWith(refreshError: err.toString()));
    }
  }

  Future<DecisionResult> decide(
    SwingSignal signal,
    String decision, {
    String? reason,
  }) async {
    final current = state.valueOrNull;
    if (current == null ||
        current.deciding.contains(signal.id) ||
        _decided.contains(signal.id)) {
      return const DecisionResult(DecisionOutcome.ignored, '');
    }
    state = AsyncData(
        current.copyWith(deciding: {...current.deciding, signal.id}));
    try {
      await ref
          .read(swingRepositoryProvider)
          .decide(arg, signal.id, decision, reason: reason);
      _decided.add(signal.id);
      _drop(signal.id);
      return DecisionResult(
          DecisionOutcome.recorded, decisionSuccessMessage(signal, decision));
    } on ApiError catch (err) {
      final code = err.statusCode;
      final detail = err.message.trim();
      if (code == 400 || code == 404 || code == 409) {
        _drop(signal.id);
        return DecisionResult(
          DecisionOutcome.noLongerPending,
          detail.isEmpty
              ? 'This signal is no longer pending — it was decided elsewhere.'
              : detail,
        );
      }
      _release(signal.id);
      if (code == 401) {
        return const DecisionResult(
            DecisionOutcome.failed, 'Session expired — please sign in again.');
      }
      if (code == 403) {
        return DecisionResult(
          DecisionOutcome.failed,
          detail.isEmpty ? 'You are not allowed to decide this signal.' : detail,
        );
      }
      return DecisionResult(DecisionOutcome.failed,
          detail.isEmpty ? 'Could not record that decision.' : detail);
    } catch (err) {
      _release(signal.id);
      return DecisionResult(
          DecisionOutcome.failed, 'Could not record that decision: $err');
    }
  }

  void _drop(String id) {
    final current = state.valueOrNull ?? const PendingSignalsState();
    state = AsyncData(current.copyWith(
      signals: current.signals.where((s) => s.id != id).toList(),
      deciding: {...current.deciding}..remove(id),
    ));
  }

  void _release(String id) {
    final current = state.valueOrNull ?? const PendingSignalsState();
    state =
        AsyncData(current.copyWith(deciding: {...current.deciding}..remove(id)));
  }
}

final pendingSignalsProvider = AsyncNotifierProvider.autoDispose
    .family<PendingSignalsNotifier, PendingSignalsState, String>(
  PendingSignalsNotifier.new,
);

// ── Wheel snapshot (pull-to-refresh) ─────────────────────────────────────────

final wheelSnapshotProvider =
    FutureProvider.autoDispose.family<WheelSnapshot, String>(
  (ref, instanceId) => ref.watch(swingRepositoryProvider).wheel(instanceId),
);
```

- [ ] **Step 7: Run the tests and the analyzer.**

Run: `cd mobile && flutter test test/features/swing && flutter analyze lib/features/swing test/features/swing`
Expected: `+16: All tests passed!` and `No issues found!`

- [ ] **Step 8: Commit.** Run `mcp__gitnexus__detect_changes()` first. Expected: new files only.

```bash
git add mobile/lib/features/swing/data/swing_repository.dart mobile/lib/features/swing/application/swing_controller.dart mobile/test/features/swing/swing_fakes.dart mobile/test/features/swing/swing_repository_test.dart mobile/test/features/swing/swing_controller_test.dart
git commit -F - <<'EOF'
feat(mobile): swing signal repository and approval controller

SwingRepository reads pending signals and the wheel snapshot and posts
decisions. PendingSignalsNotifier polls every 30 seconds while the app is in
the foreground, ignores a second tap while a decision is in flight, removes
a card the server says is no longer pending, keeps it on 401/403/5xx, and
never lets a racing poll resurrect a card this device decided.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 6: Mobile pending signals section, wheel card and instance screen

**Files:**
- Create: `mobile/lib/features/swing/presentation/pending_signals_section.dart`
- Create: `mobile/lib/features/swing/presentation/wheel_card.dart`
- Modify: `mobile/lib/features/instances/presentation/instance_detail_screen.dart:15` (imports), `:91-139` (`_DetailContent.build`)
- Test: `mobile/test/features/swing/pending_signals_section_test.dart`

**Interfaces:**
- Consumes: everything Task 5 produces; `showConfirmDialog(context, {title, body, confirmLabel, confirmColor, icon})` returning `Future<bool>`; `AppButton.semantic`, `AppButton.ghost`, `AppBadge`, `ErrorBanner`, `GlassCard`, `fmtMoney`, `fmtPnl`, `pnlColor`, `symbol()`.
- Produces:
  - `PendingSignalsSection({required String instanceId})` and `List<(String, String)> proposalFields(SwingSignal)`;
  - `WheelCard({required String instanceId})` and `String fmtItm(double?)`.

The card layout follows `learning_screen.dart`'s `_ApprovalCard` (496-560): a badge row, a body, then the buttons. Two differences:
- The buttons sit in a `Wrap`. Three labelled buttons do not fit on one line of a 320pt phone.
- The confirm dialog is called without `onConfirm`, so failures reach a SnackBar.

- [ ] **Step 0: Impact.** Run `mcp__gitnexus__impact({target: "_DetailContent", direction: "upstream"})`. Confirm with `grep -n "_DetailContent(" mobile/lib/features/instances/presentation/instance_detail_screen.dart`: one construction, in `_InstanceDetailBody.build`. The edit inserts widgets and extends `onRefresh`. Risk is LOW.

- [ ] **Step 1: Write the failing widget test** `mobile/test/features/swing/pending_signals_section_test.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/network/api_error.dart';
import 'package:intellistock_mobile/core/widgets/app_button.dart';
import 'package:intellistock_mobile/features/swing/data/swing_repository.dart';
import 'package:intellistock_mobile/features/swing/presentation/pending_signals_section.dart';
import 'package:intellistock_mobile/features/swing/presentation/wheel_card.dart';

import 'swing_fakes.dart';

Widget _app(FakeSwingRepo repo, Widget child) => ProviderScope(
      overrides: [swingRepositoryProvider.overrideWithValue(repo)],
      child: MaterialApp(
        home: Scaffold(body: SingleChildScrollView(child: child)),
      ),
    );

const _section = PendingSignalsSection(instanceId: 'i1');

Finder _cardButton(String label) => find.widgetWithText(AppButton, label);

Finder _dialogButton(String label) => find.descendant(
    of: find.byType(Dialog), matching: find.widgetWithText(AppButton, label));

void _phone(WidgetTester tester) {
  tester.view.physicalSize = const Size(320 * 3, 2400 * 3);
  tester.view.devicePixelRatio = 3.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
}

void main() {
  group('PendingSignalsSection', () {
    testWidgets('renders swing and wheel cards; Approve ½ on swing only',
        (tester) async {
      final repo = FakeSwingRepo([swingSignal('a1'), wheelSignal('w1')]);
      await tester.pumpWidget(_app(repo, _section));
      await tester.pumpAndSettle();

      expect(find.text('Pending AI signals (2)'), findsOneWidget);
      expect(find.text('AAPL'), findsOneWidget);
      expect(find.text('APH'), findsOneWidget);
      expect(_cardButton('Approve ½'), findsOneWidget);
      expect(_cardButton('Approve'), findsNWidgets(2));
      expect(find.text('APH261002P00130000'), findsOneWidget);
      expect(find.text('\$1.30 (\$130.00)'), findsOneWidget);
      expect(find.text('\$13,000.00'), findsOneWidget);
      expect(find.text('Risks: earnings in 9 days'), findsOneWidget);
    });

    testWidgets('empty list says so', (tester) async {
      await tester.pumpWidget(_app(FakeSwingRepo([]), _section));
      await tester.pumpAndSettle();
      expect(find.text('Nothing waiting for review.'), findsOneWidget);
    });

    testWidgets('approve goes through the confirm dialog, then the card leaves',
        (tester) async {
      final repo = FakeSwingRepo([swingSignal('a1')]);
      await tester.pumpWidget(_app(repo, _section));
      await tester.pumpAndSettle();

      await tester.tap(_cardButton('Approve'));
      await tester.pumpAndSettle();
      expect(find.byType(Dialog), findsOneWidget);
      expect(repo.decideCalls, isEmpty);

      await tester.tap(_dialogButton('Approve'));
      await tester.pumpAndSettle();
      expect(repo.decideCalls, ['a1:approve']);
      expect(find.text('AAPL'), findsNothing);
      expect(find.textContaining('Approved AAPL'), findsOneWidget);
    });

    testWidgets('cancel in the dialog sends nothing', (tester) async {
      final repo = FakeSwingRepo([swingSignal('a1')]);
      await tester.pumpWidget(_app(repo, _section));
      await tester.pumpAndSettle();

      await tester.tap(_cardButton('Reject'));
      await tester.pumpAndSettle();
      await tester.tap(_dialogButton('Cancel'));
      await tester.pumpAndSettle();
      expect(repo.decideCalls, isEmpty);
      expect(find.text('AAPL'), findsOneWidget);
    });

    testWidgets('decided on another device (400): card removed, reason shown',
        (tester) async {
      final repo = FakeSwingRepo([swingSignal('a1')])
        ..decideError =
            ApiError('signal a1 is approved, not pending', statusCode: 400);
      await tester.pumpWidget(_app(repo, _section));
      await tester.pumpAndSettle();

      await tester.tap(_cardButton('Approve'));
      await tester.pumpAndSettle();
      await tester.tap(_dialogButton('Approve'));
      await tester.pumpAndSettle();
      expect(find.text('AAPL'), findsNothing);
      expect(find.text('signal a1 is approved, not pending'), findsOneWidget);
    });

    testWidgets('forbidden (403): card stays and can be retried',
        (tester) async {
      final repo = FakeSwingRepo([swingSignal('a1')])
        ..decideError = ApiError('not your instance', statusCode: 403);
      await tester.pumpWidget(_app(repo, _section));
      await tester.pumpAndSettle();

      await tester.tap(_cardButton('Reject'));
      await tester.pumpAndSettle();
      await tester.tap(_dialogButton('Reject'));
      await tester.pumpAndSettle();
      expect(find.text('AAPL'), findsOneWidget);
      expect(find.text('not your instance'), findsOneWidget);
      expect(tester.widget<AppButton>(_cardButton('Reject')).onPressed,
          isNotNull);
    });

    testWidgets('long reasoning on a 320pt phone: no overflow, collapsible',
        (tester) async {
      _phone(tester);
      final long = List.filled(120, 'momentum').join(' ') + 'x' * 600;
      final repo = FakeSwingRepo([swingSignal('a1', reasoning: long)]);
      await tester.pumpWidget(_app(repo, _section));
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
      expect(find.text('Show more'), findsOneWidget);
      await tester.tap(find.text('Show more'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text('Show less'), findsOneWidget);
    });
  });

  group('WheelCard', () {
    testWidgets('lists open puts; a missing mark shows a dash', (tester) async {
      final repo = FakeSwingRepo([],
          wheelSnapshot: WheelSnapshot.fromJson({
            'open_puts': [
              {
                'contract': 'APH261002P00130000',
                'underlying': 'APH',
                'strike': 130,
                'expiry': '2026-10-02',
                'qty': 1,
                'avg_entry_price': 1.23,
                'current_price': null,
                'itm_pct': 2.0,
                'dte': 8,
                'collateral': 13000,
                'unrealized_pl': null,
              },
            ],
            'collateral_total': 13000,
            'cash': 25000,
            'recent_scans': [],
          }));
      await tester.pumpWidget(_app(repo, const WheelCard(instanceId: 'i1')));
      await tester.pumpAndSettle();

      expect(find.text('APH \$130.00 P · 2026-10-02'), findsOneWidget);
      expect(find.text('2.0% ITM'), findsOneWidget);
      expect(find.text('\$13,000.00'), findsOneWidget);
      expect(find.text('No scans recorded yet.'), findsOneWidget);
      // MARK and P&L both have no value; neither may render as $0.00.
      expect(find.text('\$0.00'), findsNothing);
      expect(find.text('—'), findsNWidgets(2));
    });

    testWidgets('an API build without the route (404) says so', (tester) async {
      final repo = FakeSwingRepo([])
        ..wheelError = ApiError('Not Found', statusCode: 404);
      await tester.pumpWidget(_app(repo, const WheelCard(instanceId: 'i1')));
      await tester.pumpAndSettle();
      expect(find.text('This API build has no wheel endpoint yet.'),
          findsOneWidget);
    });
  });

  test('fmtItm', () {
    expect(fmtItm(2.0), '2.0% ITM');
    expect(fmtItm(-3.0), '3.0% OTM');
    expect(fmtItm(null), '—');
  });
}
```

- [ ] **Step 2: Run it and watch it fail.**

Run: `cd mobile && flutter test test/features/swing/pending_signals_section_test.dart`
Expected: FAIL at load: `Error when reading 'lib/features/swing/presentation/pending_signals_section.dart': No such file or directory`.

- [ ] **Step 3: Implement** `mobile/lib/features/swing/presentation/pending_signals_section.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/confirm_dialog.dart';
import '../../../core/widgets/glass_card.dart';
import '../../../core/widgets/material_symbols.dart';
import '../application/swing_controller.dart';
import '../data/swing_repository.dart';

/// Reasoning longer than this starts collapsed behind "Show more".
const _reasoningCollapseChars = 240;

/// AI-scored swing and wheel candidates that wait for a human (spec
/// 2026-09-24 section 10). Shown on the instance detail screen above the
/// Stocks card when the strategy document has a swing or wheel lane.
class PendingSignalsSection extends ConsumerWidget {
  const PendingSignalsSection({super.key, required this.instanceId});

  final String instanceId;

  Future<void> _decide(BuildContext context, WidgetRef ref, SwingSignal signal,
      String decision) async {
    // No onConfirm callback: showConfirmDialog swallows errors thrown there,
    // and the operator has to see why a decision did not land.
    final confirmed = await showConfirmDialog(
      context,
      title: '${decisionLabel(decision)} ${signal.symbol}',
      body: decisionConfirmBody(signal, decision),
      confirmLabel: decisionLabel(decision),
      confirmColor: decision == 'reject' ? AppColors.danger : AppColors.success,
      icon: decision == 'reject' ? symbol('block') : symbol('check'),
    );
    if (!confirmed || !context.mounted) return;
    final result = await ref
        .read(pendingSignalsProvider(instanceId).notifier)
        .decide(signal, decision);
    if (!context.mounted || result.outcome == DecisionOutcome.ignored) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(result.message)));
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(pendingSignalsProvider(instanceId));
    final count = async.valueOrNull?.signals.length;
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            count == null ? 'Pending AI signals' : 'Pending AI signals ($count)',
            style: AppTextStyles.cardTitle,
          ),
          const SizedBox(height: 10),
          async.when(
            loading: () => Text('Loading…', style: AppTextStyles.meta),
            error: (err, _) => ErrorBanner(
              message: err.toString(),
              onRetry: () => ref.invalidate(pendingSignalsProvider(instanceId)),
            ),
            data: (state) => Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (state.refreshError != null) ...[
                  Text(
                    'Last refresh failed: ${state.refreshError}',
                    style: AppTextStyles.nano.copyWith(color: AppColors.warning),
                  ),
                  const SizedBox(height: 8),
                ],
                if (state.signals.isEmpty)
                  Text(
                    'Nothing waiting for review.',
                    style: AppTextStyles.meta
                        .copyWith(fontStyle: FontStyle.italic),
                  )
                else
                  for (final s in state.signals)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: _SignalCard(
                        key: ValueKey(s.id),
                        signal: s,
                        busy: state.isDeciding(s.id),
                        onDecide: (d) => _decide(context, ref, s, d),
                      ),
                    ),
                const SizedBox(height: 4),
                Text(
                  'Approval rebuilds the order at the live price.',
                  style: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

Color _scoreColor(int? score) {
  if (score == null) return AppColors.textMuted;
  if (score >= 75) return AppColors.success;
  if (score >= 50) return AppColors.warning;
  return AppColors.danger;
}

/// (label, value) pairs for the proposal grid.
List<(String, String)> proposalFields(SwingSignal s) {
  if (s.isWheel) {
    final credit = s.creditEst;
    return [
      ('CONTRACT', s.contract.isEmpty ? '—' : s.contract),
      ('STRIKE', fmtMoney(s.strike)),
      ('EXPIRY', s.expiry.isEmpty ? '—' : s.expiry),
      ('QTY', s.qty?.toString() ?? '—'),
      ('LIMIT', fmtMoney(s.limitPrice)),
      (
        'PREMIUM',
        credit == null
            ? fmtMoney(s.premiumEst)
            : '${fmtMoney(s.premiumEst)} (${fmtMoney(credit)})'
      ),
      ('COLLATERAL', fmtMoney(s.collateral)),
    ];
  }
  return [
    ('ENTRY', fmtMoney(s.entry)),
    ('STOP', fmtMoney(s.stop)),
    ('TARGET', fmtMoney(s.target)),
    ('SHARES', s.shares?.toString() ?? '—'),
  ];
}

class _SignalCard extends StatefulWidget {
  const _SignalCard({
    super.key,
    required this.signal,
    required this.busy,
    required this.onDecide,
  });

  final SwingSignal signal;
  final bool busy;
  final void Function(String decision) onDecide;

  @override
  State<_SignalCard> createState() => _SignalCardState();
}

class _SignalCardState extends State<_SignalCard> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final s = widget.signal;
    final longReasoning = s.reasoning.length > _reasoningCollapseChars;
    // Buttons go inert while this card's request is in flight.
    VoidCallback? tap(String decision) =>
        widget.busy ? null : () => widget.onDecide(decision);

    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                s.symbol,
                style: AppTextStyles.cardTitle.copyWith(
                    color: AppColors.textHi, fontWeight: FontWeight.w800),
              ),
              const SizedBox(width: 8),
              AppBadge(
                label: s.lane,
                color: s.isWheel ? AppColors.primary : AppColors.info,
              ),
              const SizedBox(width: 6),
              AppBadge(
                  label: s.score?.toString() ?? '—',
                  color: _scoreColor(s.score)),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  'session ${s.session.isEmpty ? '—' : s.session}',
                  textAlign: TextAlign.end,
                  overflow: TextOverflow.ellipsis,
                  style: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 16,
            runSpacing: 8,
            children: [
              for (final (label, value) in proposalFields(s))
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(label,
                        style: AppTextStyles.nano.copyWith(
                            color: AppColors.textDim, letterSpacing: 0.4)),
                    Text(value,
                        style: AppTextStyles.micro.copyWith(
                            color: AppColors.textMd,
                            fontWeight: FontWeight.w700)),
                  ],
                ),
            ],
          ),
          if (s.reasoning.trim().isNotEmpty) ...[
            const SizedBox(height: 10),
            Text(
              s.reasoning.trim(),
              style: AppTextStyles.micro.copyWith(color: AppColors.textMd),
              maxLines: _expanded || !longReasoning ? null : 4,
              overflow: _expanded || !longReasoning
                  ? TextOverflow.visible
                  : TextOverflow.ellipsis,
            ),
            if (longReasoning)
              TextButton(
                onPressed: () => setState(() => _expanded = !_expanded),
                style: TextButton.styleFrom(
                  padding: EdgeInsets.zero,
                  minimumSize: const Size(0, 32),
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
                child: Text(_expanded ? 'Show less' : 'Show more'),
              ),
          ],
          if (s.keyRisksText.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(
              'Risks: ${s.keyRisksText}',
              style: AppTextStyles.nano.copyWith(color: AppColors.warning),
            ),
          ],
          const SizedBox(height: 10),
          // A Wrap rather than a Row of Expanded: three labelled buttons do
          // not fit a 320pt-wide phone on one line.
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              AppButton.semantic(
                label: decisionLabel('approve'),
                color: AppColors.success,
                dense: true,
                onPressed: tap('approve'),
              ),
              if (s.allowsHalf)
                AppButton.semantic(
                  label: decisionLabel('approve_half'),
                  color: AppColors.success,
                  dense: true,
                  onPressed: tap('approve_half'),
                ),
              AppButton.ghost(
                label: decisionLabel('reject'),
                dense: true,
                onPressed: tap('reject'),
              ),
            ],
          ),
          if (widget.busy) ...[
            const SizedBox(height: 6),
            Text('Working…',
                style: AppTextStyles.nano.copyWith(color: AppColors.textDim)),
          ],
        ],
      ),
    );
  }
}
```

- [ ] **Step 4: Implement** `mobile/lib/features/swing/presentation/wheel_card.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/formatters/formatters.dart';
import '../../../core/network/api_error.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/common_widgets.dart';
import '../../../core/widgets/glass_card.dart';
import '../application/swing_controller.dart';
import '../data/swing_repository.dart';

/// "2.0% ITM" / "3.0% OTM" / "—".
String fmtItm(double? itmPct) {
  if (itmPct == null) return '—';
  return itmPct > 0
      ? '${itmPct.toStringAsFixed(1)}% ITM'
      : '${itmPct.abs().toStringAsFixed(1)}% OTM';
}

/// The wheel lane's open cash-secured puts and its latest scans. Read-only:
/// the lane buys puts back by itself, and a red ITM figure means the 15:45 ET
/// monitor will buy that put back on its next pass.
class WheelCard extends ConsumerWidget {
  const WheelCard({super.key, required this.instanceId});

  final String instanceId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(wheelSnapshotProvider(instanceId));
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Wheel', style: AppTextStyles.cardTitle),
          const SizedBox(height: 10),
          async.when(
            loading: () => Text('Loading…', style: AppTextStyles.meta),
            error: (err, _) => ErrorBanner(
              message: err is ApiError && err.statusCode == 404
                  ? 'This API build has no wheel endpoint yet.'
                  : err.toString(),
              onRetry: () => ref.invalidate(wheelSnapshotProvider(instanceId)),
            ),
            data: (w) => _WheelBody(wheel: w),
          ),
        ],
      ),
    );
  }
}

class _WheelBody extends StatelessWidget {
  const _WheelBody({required this.wheel});

  final WheelSnapshot wheel;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            _Stat(label: 'OPEN PUTS', value: '${wheel.openPuts.length}'),
            _Stat(label: 'COLLATERAL', value: fmtMoney(wheel.collateralTotal)),
            _Stat(label: 'CASH', value: fmtMoney(wheel.cash)),
          ],
        ),
        const SizedBox(height: 12),
        if (wheel.openPuts.isEmpty)
          Text('No open puts.',
              style: AppTextStyles.meta.copyWith(fontStyle: FontStyle.italic))
        else
          for (final put in wheel.openPuts) _PutRow(put: put),
        const SizedBox(height: 12),
        Text('RECENT SCANS',
            style: AppTextStyles.nano
                .copyWith(color: AppColors.textDim, letterSpacing: 0.8)),
        const SizedBox(height: 6),
        if (wheel.recentScans.isEmpty)
          Text('No scans recorded yet.',
              style: AppTextStyles.meta.copyWith(fontStyle: FontStyle.italic))
        else
          for (final scan in wheel.recentScans.take(5)) _ScanRow(scan: scan),
      ],
    );
  }
}

class _PutRow extends StatelessWidget {
  const _PutRow({required this.put});

  final WheelPut put;

  Color get _itmColor {
    if (put.itmPct == null) return AppColors.textDim;
    if (put.monitorWillBuyBack) return AppColors.danger;
    return put.itmPct! > 0 ? AppColors.warning : AppColors.success;
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '${put.underlying} ${fmtMoney(put.strike)} P · ${put.expiry}',
            style: AppTextStyles.cardTitle.copyWith(color: AppColors.textHi),
          ),
          Text(put.contract,
              style: AppTextStyles.mono(10, color: AppColors.textFaint)),
          const SizedBox(height: 6),
          Row(
            children: [
              _Stat(label: 'QTY', value: put.qty?.toString() ?? '—'),
              _Stat(label: 'ENTRY', value: fmtMoney(put.avgEntryPrice)),
              _Stat(label: 'MARK', value: fmtMoney(put.currentPrice)),
            ],
          ),
          const SizedBox(height: 4),
          Row(
            children: [
              _Stat(
                  label: 'ITM', value: fmtItm(put.itmPct), color: _itmColor),
              _Stat(label: 'DTE', value: put.dte?.toString() ?? '—'),
              _Stat(
                label: 'P&L',
                value: fmtPnl(put.unrealizedPl),
                color: put.unrealizedPl == null
                    ? AppColors.textDim
                    : pnlColor(put.unrealizedPl),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _ScanRow extends StatelessWidget {
  const _ScanRow({required this.scan});

  final WheelScan scan;

  Color get _statusColor => switch (scan.status) {
        'placed' => AppColors.success,
        'pending' => AppColors.warning,
        'rejected' => AppColors.danger,
        _ => AppColors.textMuted,
      };

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${scan.symbol} ${fmtMoney(scan.strike)} P · ${scan.expiry.isEmpty ? '—' : scan.expiry}',
                  style: AppTextStyles.micro.copyWith(color: AppColors.textMd),
                ),
                if (scan.skipReason.isNotEmpty)
                  Text(scan.skipReason,
                      style: AppTextStyles.nano
                          .copyWith(color: AppColors.textFaint)),
              ],
            ),
          ),
          const SizedBox(width: 8),
          AppBadge(
              label: scan.status.isEmpty ? '—' : scan.status,
              color: _statusColor),
        ],
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.value, this.color});

  final String label;
  final String value;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label,
              style: AppTextStyles.nano
                  .copyWith(color: AppColors.textDim, letterSpacing: 0.4)),
          const SizedBox(height: 2),
          Text(
            value,
            overflow: TextOverflow.ellipsis,
            style: AppTextStyles.micro.copyWith(
                color: color ?? AppColors.textMd, fontWeight: FontWeight.w700),
          ),
        ],
      ),
    );
  }
}
```

- [ ] **Step 5: Wire both into** `mobile/lib/features/instances/presentation/instance_detail_screen.dart`, with three edits.

5a. Replace
```dart
import '../../../core/widgets/typed_confirm_field.dart';
```
with
```dart
import '../../../core/widgets/typed_confirm_field.dart';
import '../../swing/application/swing_controller.dart';
import '../../swing/presentation/pending_signals_section.dart';
import '../../swing/presentation/wheel_card.dart';
```

5b. Replace
```dart
    final inst = state.instance!;
    final ctrl = ref.read(instanceDetailControllerProvider(instanceId).notifier);

    return RefreshIndicator(
      onRefresh: () async {
        await ctrl.refreshInstance();
      },
```
with
```dart
    final inst = state.instance!;
    final ctrl = ref.read(instanceDetailControllerProvider(instanceId).notifier);
    final lanes = swingLanesOf(inst.strategy);

    return RefreshIndicator(
      onRefresh: () async {
        await ctrl.refreshInstance();
        if (lanes.any) ref.invalidate(pendingSignalsProvider(instanceId));
        if (lanes.wheel) ref.invalidate(wheelSnapshotProvider(instanceId));
      },
```

5c. Replace
```dart
                  _StrategyCard(instanceId: instanceId, inst: inst),
                  const SizedBox(height: 12),
                  // Stocks
```
with
```dart
                  _StrategyCard(instanceId: instanceId, inst: inst),
                  const SizedBox(height: 12),
                  // Swing / wheel lanes: AI signals awaiting approval, and the
                  // wheel's open puts (spec 2026-09-24 section 10).
                  if (lanes.any) ...[
                    PendingSignalsSection(instanceId: instanceId),
                    const SizedBox(height: 12),
                  ],
                  if (lanes.wheel) ...[
                    WheelCard(instanceId: instanceId),
                    const SizedBox(height: 12),
                  ],
                  // Stocks
```

- [ ] **Step 6: Run the tests and the analyzer.**

Run: `cd mobile && flutter test test/features/swing && flutter analyze lib/features/swing lib/features/instances test/features/swing`
Expected:
- `+26: All tests passed!`
- The analyzer reports only the 2 pre-existing `deprecated_member_use` infos in `instance_detail_screen.dart` (`groupValue`/`onChanged`, which sit at lines 1230-1231 before this edit). Nothing new.

- [ ] **Step 7: Manual check on the simulator or device.** It needs plan B's routes.
  1. Run `cd mobile && flutter run`. Open an instance whose strategy has only `strategy_eb`: no swing section.
  2. Open `swing-paper`. "Pending AI signals" sits above "Stocks"; "Wheel" shows when `strategy_wheel` is on the document.
  3. Approve on iOS and confirm. The card goes and a SnackBar says "Approved ...". On web, the same signal is gone within 30 s.
  4. Pull to refresh reloads both cards.

- [ ] **Step 8: Commit.** Run `mcp__gitnexus__detect_changes()` first. Expected: two new widgets, and `_DetailContent.build` in `instance_detail_screen.dart`.

```bash
git add mobile/lib/features/swing/presentation/pending_signals_section.dart mobile/lib/features/swing/presentation/wheel_card.dart mobile/lib/features/instances/presentation/instance_detail_screen.dart mobile/test/features/swing/pending_signals_section_test.dart
git commit -F - <<'EOF'
feat(mobile): pending AI signals and wheel card on the instance screen

The instance detail screen shows the pending AI signals above Stocks when
the strategy document has a swing or wheel lane, and the wheel card when it
has a wheel lane. Each decision goes through the shared confirm dialog and
reports its outcome in a snackbar; long reasoning collapses behind Show
more and the buttons wrap on narrow phones. Pull to refresh reloads both.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 7: Mobile option positions, fills and sector breakdown

**Files:**
- Create: `mobile/lib/core/models/option_symbol.dart`
- Modify: `mobile/lib/features/live_trading/data/models/live_state.dart:1-3` (import), `:73-133` (`Position`, `Trade`)
- Modify: `mobile/lib/features/live_trading/presentation/position_card.dart:6, 30, 83-104, 157, 169, 182-184, 191-201`
- Modify: `mobile/lib/features/live_trading/application/live_state_notifier.dart:193-197` (`_refreshPositionHistoricals`)
- Modify: `mobile/lib/features/live_trading/presentation/live_trading_screen.dart:1322-1384` (`_TradeRow`)
- Modify: `mobile/lib/features/dashboard/application/portfolio_analytics.dart:5, 15-25` (`aggregateBySector`)
- Modify: `mobile/lib/features/dashboard/application/insights_controller.dart:5, 280` (`sectorAllocationProvider`)
- Test: `mobile/test/core/models/option_symbol_test.dart`, `mobile/test/features/live_trading/live_state_options_test.dart`, `mobile/test/features/live_trading/position_card_test.dart`, `mobile/test/features/dashboard/portfolio_analytics_test.dart` (one new test)

**Interfaces:**
- Consumes: the live-state position fields (contract addendum item 4).
- Produces:
  - `option_symbol.dart`: `kOptionMultiplier = 100`, `OccContract`, `OccContract? parseOccSymbol(String?)`, `bool isOccOptionSymbol(String?)`, and `String describeOptionContract({required String symbol, String? underlying, double? strike, String? optionType, String? expiry})`.
  - `Position`:
    - `marketValue`, `lastPrice`, `unrealizedPnl` and `unrealizedPnlPct` become `double?`;
    - new fields `assetClass`, `side`, `multiplier`, `underlying`, `strike`, `expiry`;
    - new getters `isOption`, `isShort`, `contractMultiplier`, `quantityLabel`, `quantityText`, `canClose`, `optionDescription`.
  - `Trade`: new field `assetClass`; new getters `isOption`, `quantityLabel`, `quantityText`, `total`.

**Decision: options are excluded from the sector breakdown, long or short.** `portfolio_analytics.dart:25` already drops a short put, because its value is negative. A long option would slip through as its premium. Neither number is sector exposure: a short put commits strike × 100 of the underlying's sector. So both are excluded explicitly, the reason is written into the function's doc comment, and a test pins it. Concentration (`concentration()`) is unchanged; its `v > 0` filter already drops short puts.

- [ ] **Step 0: Impact.** Run `mcp__gitnexus__impact` upstream on `Position`, `Trade` (`live_state.dart`), `PositionCard`, `_TradeRow`, `LiveStateNotifier`, `aggregateBySector` and `sectorAllocationProvider`. Confirm with grep:

```bash
grep -rn "\.lastPrice\|\.unrealizedPnl\b\|\.marketValue\b\|\.unrealizedPnlPct" mobile/lib/features/live_trading
grep -rn "live_state.dart" mobile/lib | grep import
grep -rn "aggregateBySector" mobile/lib mobile/test
```
Expected:
- The nullable `Position` fields are read only in `position_card.dart`.
- `live_state.dart` is imported by `live_state_notifier.dart`, `live_repository.dart`, `live_trading_screen.dart`, `position_card.dart`, `stock_screen.dart` and `stock_controller.dart`. The two `stock/` files use `Trade.qty`/`Trade.price` only, and `Trade`'s changes are additive.
- `aggregateBySector` is called by `insights_controller.dart:302` and its test.
- The iOS home-screen widget (`widget_sync_service.dart:149`) parses raw JSON, not `Position`, so it is unaffected.

Risk is LOW to MEDIUM: nullability ripples, and the analyzer catches every one.

- [ ] **Step 1: Write the failing tests.**

`mobile/test/core/models/option_symbol_test.dart`:
```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/models/option_symbol.dart';

void main() {
  test('parseOccSymbol reads root, expiry, type and strike', () {
    final put = parseOccSymbol('APH261002P00130000')!;
    expect(put.underlying, 'APH');
    expect(put.expiry, '2026-10-02');
    expect(put.optionType, 'put');
    expect(put.strike, 130.0);
    final call = parseOccSymbol('spy261218c00612500')!;
    expect(call.underlying, 'SPY');
    expect(call.optionType, 'call');
    expect(call.strike, 612.5);
  });

  test('stock tickers and junk are not options', () {
    for (final s in ['AAPL', 'BRK.B', '', null, 'APH261002X00130000',
        'TOOLONGROOT261002P00130000']) {
      expect(isOccOptionSymbol(s), isFalse, reason: '$s');
    }
  });

  test('describeOptionContract prefers explicit fields, falls back to the symbol', () {
    expect(describeOptionContract(symbol: 'APH261002P00130000'),
        'APH \$130 Put · 2026-10-02');
    expect(
        describeOptionContract(
            symbol: 'APH261002P00130000', underlying: 'APH', strike: 127.5),
        'APH \$127.50 Put · 2026-10-02');
    expect(describeOptionContract(symbol: 'AAPL'), '');
  });
}
```

`mobile/test/features/live_trading/live_state_options_test.dart`:
```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/live_trading/data/models/live_state.dart';

// A live-state position after plan A-live: Alpaca had no quote for the put.
const _shortPut = <String, dynamic>{
  'symbol': 'APH261002P00130000',
  'qty': -1,
  'avg_entry_price': 1.23,
  'last_price': null,
  'market_value': null,
  'unrealized_pnl': null,
  'unrealized_pnl_pct': null,
  'asset_class': 'us_option',
  'side': 'short',
  'multiplier': 100,
  'underlying': 'APH',
  'strike': 130,
  'expiry': '2026-10-02',
};

void main() {
  group('Position', () {
    test('a short put with no quote keeps its nulls', () {
      final p = Position.fromJson(_shortPut);
      expect(p.isOption, isTrue);
      expect(p.isShort, isTrue);
      expect(p.contractMultiplier, 100);
      expect(p.quantityLabel, 'CONTRACTS');
      expect(p.quantityText, '1');
      expect(p.canClose, isFalse);
      expect(p.lastPrice, isNull);
      expect(p.marketValue, isNull);
      expect(p.unrealizedPnl, isNull);
      expect(p.optionDescription, 'APH \$130 Put · 2026-10-02');
    });

    test('an equity row from today\'s API parses as before', () {
      final p = Position.fromJson(const {
        'symbol': 'AAPL',
        'qty': 12.5,
        'avg_entry_price': 190.0,
        'last_price': 200.0,
        'market_value': 2500.0,
        'unrealized_pnl': 125.0,
        'unrealized_pnl_pct': 5.26,
      });
      expect(p.isOption, isFalse);
      expect(p.isShort, isFalse);
      expect(p.contractMultiplier, 1);
      expect(p.quantityLabel, 'SHARES');
      expect(p.quantityText, '12.5000');
      expect(p.canClose, isTrue);
      expect(p.marketValue, 2500.0);
      expect(p.optionDescription, '');
    });

    test('an OCC symbol without asset_class is still an option', () {
      final p = Position.fromJson(const {'symbol': 'APH261002P00130000', 'qty': -2});
      expect(p.isOption, isTrue);
      expect(p.isShort, isTrue);
      expect(p.contractMultiplier, 100);
    });
  });

  group('Trade', () {
    test('an option fill totals x100 and counts contracts', () {
      final t = Trade.fromJson(const {
        'symbol': 'APH261002P00130000', 'side': 'sell', 'qty': 1, 'price': 1.25,
      });
      expect(t.isOption, isTrue);
      expect(t.quantityLabel, 'CONTRACTS');
      expect(t.quantityText, '1');
      expect(t.total, 125.0);
    });

    test('a stock fill is unchanged', () {
      final t = Trade.fromJson(const {
        'symbol': 'AAPL', 'side': 'buy', 'qty': 12, 'price': 200.0,
      });
      expect(t.isOption, isFalse);
      expect(t.quantityLabel, 'SHARES');
      expect(t.quantityText, '12.0000');
      expect(t.total, 2400.0);
    });
  });
}
```

`mobile/test/features/live_trading/position_card_test.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/live_trading/data/models/live_state.dart';
import 'package:intellistock_mobile/features/live_trading/presentation/equity_chart.dart';
import 'package:intellistock_mobile/features/live_trading/presentation/position_card.dart';

Widget _card(Position p, {VoidCallback? onClose}) => MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: PositionCard(
            position: p,
            chartStyle: ChartStyle.area,
            range: '1D',
            historicals: const [],
            onClose: onClose ?? () {},
          ),
        ),
      ),
    );

void main() {
  testWidgets('short put with no quote: contracts, badges, dashes, no Close',
      (tester) async {
    tester.view.physicalSize = const Size(320 * 3, 1200 * 3);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(_card(Position.fromJson(const {
      'symbol': 'APH261002P00130000',
      'qty': -1,
      'avg_entry_price': 1.23,
      'last_price': null,
      'market_value': null,
      'unrealized_pnl': null,
      'unrealized_pnl_pct': null,
      'asset_class': 'us_option',
      'side': 'short',
      'multiplier': 100,
      'underlying': 'APH',
      'strike': 130,
      'expiry': '2026-10-02',
    })));
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    expect(find.text('OPTION'), findsOneWidget);
    expect(find.text('SHORT'), findsOneWidget);
    expect(find.text('APH \$130 Put · 2026-10-02'), findsOneWidget);
    expect(find.text('CONTRACTS'), findsOneWidget);
    expect(find.text('SHARES'), findsNothing);
    expect(find.text('1'), findsOneWidget);
    expect(find.text('No price chart for options'), findsOneWidget);
    expect(find.text('\$0.00'), findsNothing);
    // MARKET VALUE, LAST and P&L $ have no value to show.
    expect(find.text('—'), findsNWidgets(3));
    expect(find.text('Close'), findsNothing);
    expect(find.text('Managed by the wheel lane. Use Halt to stop it.'),
        findsOneWidget);
  });

  testWidgets('stock position keeps Shares and Close', (tester) async {
    var closed = false;
    await tester.pumpWidget(_card(
      Position.fromJson(const {
        'symbol': 'AAPL',
        'qty': 12,
        'avg_entry_price': 190.0,
        'last_price': 200.0,
        'market_value': 2400.0,
        'unrealized_pnl': 120.0,
        'unrealized_pnl_pct': 5.26,
      }),
      onClose: () => closed = true,
    ));
    await tester.pumpAndSettle();

    expect(find.text('SHARES'), findsOneWidget);
    expect(find.text('12.0000'), findsOneWidget);
    expect(find.text('OPTION'), findsNothing);
    expect(find.text('\$2,400.00'), findsOneWidget);
    await tester.tap(find.text('Close'));
    expect(closed, isTrue);
  });
}
```

In `mobile/test/features/dashboard/portfolio_analytics_test.dart`, inside `group('aggregateBySector', ...)`, insert right before `test('empty input → empty list', () {`:
```dart
    test('option contracts are excluded, long or short', () {
      final slices = aggregateBySector(
        {'AAPL': 60, 'APH261002P00130000': -85, 'SPY261218C00612500': 400},
        {
          'AAPL': 'Technology',
          'APH261002P00130000': 'Technology',
          'SPY261218C00612500': 'Other',
        },
      );
      expect(slices.single.sector, 'Technology');
      expect(slices.single.value, 60);
      expect(slices.single.pct, closeTo(100, 0.001));
    });

```

- [ ] **Step 2: Run them and watch them fail.**

Run: `cd mobile && flutter test test/core/models test/features/live_trading test/features/dashboard/portfolio_analytics_test.dart`
Expected:
- The first three files fail to compile: `option_symbol.dart` does not exist, and `The getter 'isOption' isn't defined for the type 'Position'`.
- `option contracts are excluded, long or short` fails: `Expected: 'Technology' Actual: 'Other'`, because the long call is still counted.

- [ ] **Step 3: Create** `mobile/lib/core/models/option_symbol.dart`:

```dart
/// OCC option-symbol helpers. Pure Dart, no Flutter.
///
/// Live-state positions carry `asset_class` (spec 2026-09-24 section 6.1), so
/// these are a display fallback for rows that do not: recent trades and the
/// dashboard's /brokerages/{id}/positions holdings. The engine identifies
/// contracts by Alpaca's contract fields, never by this shape (spec fix 10).
library;

/// Shares per US equity option contract.
const kOptionMultiplier = 100;

// Root (1-6), YYMMDD, C|P, strike x 1000 in 8 digits.
final _occ = RegExp(r'^([A-Z][A-Z0-9]{0,5})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$');

class OccContract {
  const OccContract({
    required this.underlying,
    required this.expiry,
    required this.optionType,
    required this.strike,
  });

  final String underlying;

  /// YYYY-MM-DD.
  final String expiry;

  /// "put" | "call".
  final String optionType;
  final double strike;
}

OccContract? parseOccSymbol(String? symbol) {
  final m = _occ.firstMatch((symbol ?? '').trim().toUpperCase());
  if (m == null) return null;
  return OccContract(
    underlying: m[1]!,
    expiry: '20${m[2]}-${m[3]}-${m[4]}',
    optionType: m[5] == 'P' ? 'put' : 'call',
    strike: int.parse(m[6]!) / 1000,
  );
}

bool isOccOptionSymbol(String? symbol) => parseOccSymbol(symbol) != null;

/// "APH $130 Put · 2026-10-02". Explicit fields win; the symbol fills gaps.
String describeOptionContract({
  required String symbol,
  String? underlying,
  double? strike,
  String? optionType,
  String? expiry,
}) {
  final occ = parseOccSymbol(symbol);
  final u = (underlying?.isNotEmpty ?? false) ? underlying! : (occ?.underlying ?? '');
  final k = strike ?? occ?.strike;
  final t = (optionType?.isNotEmpty ?? false) ? optionType! : (occ?.optionType ?? '');
  final e = (expiry?.isNotEmpty ?? false) ? expiry! : (occ?.expiry ?? '');
  final parts = <String>[
    if (u.isNotEmpty) u,
    if (k != null)
      k == k.roundToDouble()
          ? '\$${k.toStringAsFixed(0)}'
          : '\$${k.toStringAsFixed(2)}',
    if (t.isNotEmpty) t.toLowerCase() == 'put' ? 'Put' : 'Call',
  ];
  return e.isEmpty ? parts.join(' ') : '${parts.join(' ')} · $e';
}
```

- [ ] **Step 4: Update** `mobile/lib/features/live_trading/data/models/live_state.dart`.

4a. Replace
```dart
/// All fromJson factories are nullable-tolerant.
library;
```
with
```dart
/// All fromJson factories are nullable-tolerant.
library;

import '../../../../core/models/option_symbol.dart';
```

4b. Replace everything from `class Position {` (line 73) up to, but not including, `class Lookback {` (line 134) with:
```dart
class Position {
  const Position({
    required this.symbol,
    required this.qty,
    this.marketValue,
    this.lastPrice,
    this.avgEntryPrice,
    this.unrealizedPnl,
    this.unrealizedPnlPct,
    this.assetClass,
    this.side,
    this.multiplier,
    this.underlying,
    this.strike,
    this.expiry,
  });

  final String symbol;

  /// Signed: negative for a short option.
  final double qty;

  /// Broker-reported dollars, contract multiplier already included. Null when
  /// the broker has no quote (an illiquid option): render a dash, never 0.
  final double? marketValue;

  /// Per share. For an option, the per-share premium.
  final double? lastPrice;
  final double? avgEntryPrice;
  final double? unrealizedPnl;
  final double? unrealizedPnlPct;

  /// "us_equity" | "us_option" (spec 2026-09-24 section 6.1). Null from an
  /// API build that predates the field.
  final String? assetClass;

  /// "long" | "short".
  final String? side;
  final int? multiplier;
  final String? underlying;
  final double? strike;

  /// YYYY-MM-DD.
  final String? expiry;

  bool get isOption => (assetClass?.isNotEmpty ?? false)
      ? assetClass!.toLowerCase() == 'us_option'
      : isOccOptionSymbol(symbol);

  bool get isShort =>
      (side?.isNotEmpty ?? false) ? side!.toLowerCase() == 'short' : qty < 0;

  int get contractMultiplier {
    if (!isOption) return 1;
    final m = multiplier;
    return (m != null && m > 0) ? m : kOptionMultiplier;
  }

  String get quantityLabel => isOption ? 'CONTRACTS' : 'SHARES';

  /// Options: unsigned whole contracts (the SHORT badge carries the sign).
  String get quantityText =>
      isOption ? qty.abs().truncate().toString() : qty.toStringAsFixed(4);

  /// The close_position command reads the adapter's equity book only, so a
  /// Close on an option would fail. The wheel lane manages its own buy-backs.
  bool get canClose => !isOption;

  String get optionDescription => isOption
      ? describeOptionContract(
          symbol: symbol,
          underlying: underlying,
          strike: strike,
          expiry: expiry,
        )
      : '';

  factory Position.fromJson(Map<String, dynamic> json) {
    return Position(
      symbol: (json['symbol'] as String?) ?? '',
      qty: (json['qty'] as num?)?.toDouble() ?? 0,
      marketValue: (json['market_value'] as num?)?.toDouble(),
      lastPrice: (json['last_price'] as num?)?.toDouble(),
      avgEntryPrice: (json['avg_entry_price'] as num?)?.toDouble(),
      unrealizedPnl: (json['unrealized_pnl'] as num?)?.toDouble(),
      unrealizedPnlPct: (json['unrealized_pnl_pct'] as num?)?.toDouble(),
      assetClass: json['asset_class'] as String?,
      side: json['side'] as String?,
      multiplier: (json['multiplier'] as num?)?.toInt(),
      underlying: json['underlying'] as String?,
      strike: (json['strike'] as num?)?.toDouble(),
      expiry: json['expiry'] as String?,
    );
  }
}

class Trade {
  const Trade({
    required this.side,
    required this.symbol,
    required this.price,
    required this.qty,
    this.ts,
    this.orderId,
    this.assetClass,
  });

  final String side;
  final String symbol;

  /// Per share. For an option fill, the per-share premium.
  final double price;
  final double qty;
  final dynamic ts; // ISO string or epoch
  final String? orderId;

  /// Not sent by today's recent_trades; read if a later build adds it.
  final String? assetClass;

  bool get isOption => (assetClass?.isNotEmpty ?? false)
      ? assetClass!.toLowerCase() == 'us_option'
      : isOccOptionSymbol(symbol);

  String get quantityLabel => isOption ? 'CONTRACTS' : 'SHARES';

  String get quantityText =>
      isOption ? qty.abs().truncate().toString() : qty.toStringAsFixed(4);

  /// Fill value in dollars: price x qty, x100 for an option contract.
  double get total => price * qty * (isOption ? kOptionMultiplier : 1);

  factory Trade.fromJson(Map<String, dynamic> json) {
    return Trade(
      side: (json['side'] as String?) ?? '',
      symbol: (json['symbol'] as String?) ?? '',
      price: (json['price'] as num?)?.toDouble() ?? 0,
      qty: (json['qty'] as num?)?.toDouble() ?? 0,
      ts: json['ts'],
      orderId: json['order_id'] as String?,
      assetClass: json['asset_class'] as String?,
    );
  }
}

```

- [ ] **Step 5: Update** `mobile/lib/features/live_trading/presentation/position_card.dart`. Every `old` block below occurs exactly once.

5a. Replace
```dart
import '../../../core/widgets/app_button.dart';
```
with
```dart
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/common_widgets.dart';
```

5b. Replace
```dart
    if (historicals.length < 2) return position.unrealizedPnl >= 0;
```
with
```dart
    if (historicals.length < 2) return (position.unrealizedPnl ?? 0) >= 0;
```

5c. Replace the header row (lines 83-104):
```dart
                    Row(
                      children: [
                        Text(
                          position.symbol,
                          style: AppTextStyles.cardTitle.copyWith(
                            color: AppColors.textHi,
                            fontWeight: FontWeight.w800,
                            letterSpacing: 0.5,
                          ),
                        ),
                        const SizedBox(width: 8),
                        if (position.avgEntryPrice != null)
                          Text(
                            fmtPct(position.unrealizedPnlPct),
                            style: AppTextStyles.micro.copyWith(
                              color: unrealizedPnlColor,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                      ],
                    ),
```
with
```dart
                    // A Wrap, not a Row: an 18-character OCC symbol plus two
                    // badges does not fit one line on a 320pt phone.
                    Wrap(
                      spacing: 8,
                      runSpacing: 4,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        Text(
                          position.symbol,
                          style: AppTextStyles.cardTitle.copyWith(
                            color: AppColors.textHi,
                            fontWeight: FontWeight.w800,
                            letterSpacing: 0.5,
                          ),
                        ),
                        if (position.isOption)
                          const AppBadge(label: 'Option', color: AppColors.primary),
                        if (position.isOption && position.isShort)
                          const AppBadge(label: 'Short', color: AppColors.warning),
                        if (position.avgEntryPrice != null &&
                            position.unrealizedPnlPct != null)
                          Text(
                            fmtPct(position.unrealizedPnlPct),
                            style: AppTextStyles.micro.copyWith(
                              color: unrealizedPnlColor,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                      ],
                    ),
                    if (position.isOption) ...[
                      const SizedBox(height: 2),
                      Text(
                        position.optionDescription,
                        style: AppTextStyles.nano.copyWith(color: AppColors.textDim),
                      ),
                    ],
```

5d. Replace
```dart
                    child: Text(
                      'No price history',
```
with
```dart
                    child: Text(
                      position.isOption
                          ? 'No price chart for options'
                          : 'No price history',
```

5e. Replace
```dart
              _Stat(label: 'SHARES', value: position.qty.toStringAsFixed(4)),
```
with
```dart
              _Stat(label: position.quantityLabel, value: position.quantityText),
```

5f. Replace
```dart
                valueColor: position.avgEntryPrice != null
                    ? unrealizedPnlColor
                    : AppColors.textDim,
```
with
```dart
                valueColor: position.avgEntryPrice != null &&
                        position.unrealizedPnl != null
                    ? unrealizedPnlColor
                    : AppColors.textDim,
```

5g. Replace
```dart
          // Close button
          Align(
            alignment: Alignment.centerRight,
            child: AppButton.semantic(
              label: 'Close',
              icon: symbol('logout'),
              color: AppColors.danger,
              dense: true,
              onPressed: onClose,
            ),
          ),
```
with
```dart
          // Close button. close_position cannot close an option; the wheel
          // lane buys its puts back itself.
          Align(
            alignment: Alignment.centerRight,
            child: position.canClose
                ? AppButton.semantic(
                    label: 'Close',
                    icon: symbol('logout'),
                    color: AppColors.danger,
                    dense: true,
                    onPressed: onClose,
                  )
                : Text(
                    'Managed by the wheel lane. Use Halt to stop it.',
                    style: AppTextStyles.nano.copyWith(color: AppColors.textFaint),
                  ),
          ),
```

The Market Value (`fmtMoney(position.marketValue)`), Last (`fmtMoney(position.lastPrice)`) and P&L (`fmtPnl(position.unrealizedPnl)`) calls stay as they are: all three formatters take `num?` and print `—` for null.

- [ ] **Step 6: No historicals for OCC symbols.** In `mobile/lib/features/live_trading/application/live_state_notifier.dart` (`_refreshPositionHistoricals`), replace
```dart
    final symbols =
        prev.liveState?.positions.map((p) => p.symbol).toList() ?? [];
```
with
```dart
    // Stock only: /symbol-historicals has nothing for an OCC contract.
    final symbols = prev.liveState?.positions
            .where((p) => !p.isOption)
            .map((p) => p.symbol)
            .toList() ??
        [];
```

- [ ] **Step 7: Update `_TradeRow`** in `mobile/lib/features/live_trading/presentation/live_trading_screen.dart`. The file already imports `common_widgets.dart`, which provides `AppBadge`.

7a. Replace
```dart
    final total = trade.price * trade.qty;
```
with
```dart
    final total = trade.total;
```

7b. Replace
```dart
              Text(
                trade.symbol,
                style: AppTextStyles.cardTitle.copyWith(
                  color: AppColors.textHi,
                  fontWeight: FontWeight.w800,
                ),
              ),
              const Spacer(),
```
with
```dart
              Flexible(
                child: Text(
                  trade.symbol,
                  overflow: TextOverflow.ellipsis,
                  style: AppTextStyles.cardTitle.copyWith(
                    color: AppColors.textHi,
                    fontWeight: FontWeight.w800,
                  ),
                ),
              ),
              if (trade.isOption) ...[
                const SizedBox(width: 6),
                const AppBadge(label: 'Option', color: AppColors.primary),
              ],
              const Spacer(),
```

7c. Replace
```dart
              _TradeField(
                label: 'SHARES',
                value: trade.qty.toStringAsFixed(4),
              ),
```
with
```dart
              _TradeField(
                label: trade.quantityLabel,
                value: trade.quantityText,
              ),
```

- [ ] **Step 8: Exclude options from the sector breakdown.**

8a. `mobile/lib/features/dashboard/application/portfolio_analytics.dart`. Replace
```dart
import 'dart:math' as math;
```
with
```dart
import 'dart:math' as math;

import '../../../core/models/option_symbol.dart';
```

8b. In the same file, replace
```dart
/// Group holdings' market value by sector, biggest first. Unknown/blank sectors
/// fold into "Other". Returns [] when there's no positive value.
List<SectorSlice> aggregateBySector(
  Map<String, double> valueBySymbol,
  Map<String, String?> sectorBySymbol,
) {
  final bySector = <String, double>{};
  var total = 0.0;
  valueBySymbol.forEach((sym, val) {
    if (val <= 0) return;
```
with
```dart
/// Group holdings' market value by sector, biggest first. Unknown/blank sectors
/// fold into "Other". Returns [] when there's no positive value.
///
/// Option contracts (OCC symbols) are left out on purpose, long or short. An
/// option's market value is its premium, not sector exposure: a short put
/// reports a negative value while it commits strike x 100 of the
/// underlying's sector. Counting either number would misstate the breakdown,
/// so the chart shows the stock book only.
List<SectorSlice> aggregateBySector(
  Map<String, double> valueBySymbol,
  Map<String, String?> sectorBySymbol,
) {
  final bySector = <String, double>{};
  var total = 0.0;
  valueBySymbol.forEach((sym, val) {
    if (isOccOptionSymbol(sym)) return;
    if (val <= 0) return;
```

8c. `mobile/lib/features/dashboard/application/insights_controller.dart`. Replace
```dart
import '../../../core/network/api_client.dart';
```
with
```dart
import '../../../core/models/option_symbol.dart';
import '../../../core/network/api_client.dart';
```

8d. In the same file (`sectorAllocationProvider`), replace
```dart
      final positions = holdings.positions
          .where((p) => p.symbol.isNotEmpty && p.marketValue > 0)
          .toList();
```
with
```dart
      // Options never reach the sector chart (see aggregateBySector); skip
      // them here too so no /symbols/{occ}/info lookup is made for them.
      final positions = holdings.positions
          .where((p) =>
              p.symbol.isNotEmpty &&
              p.marketValue > 0 &&
              !isOccOptionSymbol(p.symbol))
          .toList();
```

- [ ] **Step 9: Run the tests and the analyzer.**

Run: `cd mobile && flutter test test/core/models test/features/live_trading test/features/dashboard/portfolio_analytics_test.dart && flutter analyze lib/core/models lib/features/live_trading lib/features/dashboard test/core/models test/features/live_trading`
Expected:
- `All tests passed!` (52 tests across those paths).
- The analyzer reports only the pre-existing `unintended_html_in_doc_comment` info in `live_state_notifier.dart`. It sat at line 326 and moves to line 330 after Step 6. Nothing new.

- [ ] **Step 10: Manual check on a device.** It needs plan A-live's fields and a paper short put.
  1. Live Trading on `swing-paper`: the put card shows OPTION and SHORT badges, "CONTRACTS 1", "No price chart for options", and no Close.
  2. A sell-to-open fill shows OPTION, CONTRACTS, and a total of premium × 100.
  3. The dashboard sector breakdown for the paper brokerage shows only stock sectors.

- [ ] **Step 11: Commit.** Run `mcp__gitnexus__detect_changes()` first. Expected: `Position`, `Trade`, `PositionCard`, `_TradeRow`, `_refreshPositionHistoricals`, `aggregateBySector` and `sectorAllocationProvider`, plus one new file.

```bash
git add mobile/lib/core/models/option_symbol.dart mobile/lib/features/live_trading/data/models/live_state.dart mobile/lib/features/live_trading/presentation/position_card.dart mobile/lib/features/live_trading/application/live_state_notifier.dart mobile/lib/features/live_trading/presentation/live_trading_screen.dart mobile/lib/features/dashboard/application/portfolio_analytics.dart mobile/lib/features/dashboard/application/insights_controller.dart mobile/test/core/models/option_symbol_test.dart mobile/test/features/live_trading/live_state_options_test.dart mobile/test/features/live_trading/position_card_test.dart mobile/test/features/dashboard/portfolio_analytics_test.dart
git commit -F - <<'EOF'
feat(mobile): option-aware positions, fills and sector breakdown

Positions read asset_class, side, multiplier, underlying, strike and expiry;
option cards show Option and Short badges, contracts instead of shares, a
contract description, and no Close button (close_position only reads the
equity book). A missing broker quote now renders as a dash rather than
0.00. Option fills total times 100, no price history is requested for OCC
symbols, and options are excluded from the dashboard sector breakdown.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

## Final verification

- [ ] Run the complete gates:

```bash
cd frontend && npm test && npm run build
# ℹ pass 37, ℹ fail 0; ✓ built in ...
cd ../mobile && flutter analyze 2>&1 | tail -1
# 23 issues found.   (the unchanged info-only baseline)
flutter test 2>&1 | tail -1
# +430 -2: Some tests failed.   (393 baseline + 37 new; the 2 failures are the pre-existing goldens)
```

- [ ] Restore the golden-failure artifacts the full run rewrote. Never stage them.

```bash
cd .. && git checkout -- mobile/test/features/dashboard/failures/strategy_trends_card_isolatedDiff.png mobile/test/features/dashboard/failures/strategy_trends_card_maskedDiff.png mobile/test/features/dashboard/failures/strategy_trends_card_masterImage.png mobile/test/features/dashboard/failures/strategy_trends_card_testImage.png
rm -f mobile/test/features/dashboard/failures/sector_3d_drilled_isolatedDiff.png mobile/test/features/dashboard/failures/sector_3d_drilled_maskedDiff.png mobile/test/features/dashboard/failures/sector_3d_drilled_masterImage.png mobile/test/features/dashboard/failures/sector_3d_drilled_testImage.png
git status --short   # only AGENTS.md / CLAUDE.md / untracked docs from before; nothing from this plan left unstaged
```

- [ ] Run `mcp__gitnexus__detect_changes({scope: "compare", base_ref: "main"})`. Expected: frontend `strategyConfig.js`, `InstanceDetailView.vue` and `LiveTradingView.vue`; mobile `instance_detail_screen.dart`, `live_state.dart`, `position_card.dart`, `live_state_notifier.dart`, `live_trading_screen.dart`, `portfolio_analytics.dart` and `insights_controller.dart`; plus new files. No `backend/` path. (Plans A and B, if already merged into the branch, add their own.)

## Spec coverage

| Spec requirement | Task |
|---|---|
| §8: `strategyConfig.js` gains `KNOWN_LLM_ROLE_LABELS['conviction_']` and `STRATEGY_FIELD_META` labels for both strategies | 1 |
| §2 / §8: the model is linked in the strategy editor (`conviction_llm_model_id`) | 1 (confirmed through `applyStrategyLlmDraft`; no new path) |
| §10 Web: `components/swing/PendingSignalsCard.vue` and `WheelPanel.vue` on `InstanceDetailView.vue`, shown when the doc has a lane | 2, 3 |
| §10 API consumed: signals list, decision, wheel | 2, 3, 5 (contract addendum) |
| §10 Web `LiveTradingView.vue`: option badge; Contracts; ×100 totals; Close hidden for short options; no price history for OCC symbols | 4 (Close hidden for every option; see the decision in Task 4) |
| §10 Mobile: `features/swing/` repository, controller, `pending_signals_section.dart`, `wheel_card.dart` in `instance_detail_screen.dart` | 5, 6 |
| §10 Mobile: `position_card.dart` and `live_state.dart` handle options | 7 |
| §6.1: position fields `asset_class, side, multiplier, underlying, strike, expiry`, and a P&L that may be null | 4, 7 |
| §11: frontend passes `npm run build`, mobile passes `flutter analyze` | every task's gate |
| §1 success item 3: approvals on web or iOS act within seconds | UI side: the POST goes out on confirm. The "seconds" is plan B (enqueue) and A-live (LiveCommand pickup). |

**Not covered here, deliberately (other plans, or open gaps to raise):**

1. **No UI reads `GET /instances/{id}/swing/calibration`.** Spec §10 lists the route but names no screen for it.
2. **Mobile `features/strategies/strategy_config.dart` (the port of `strategyConfig.js`) is not updated.** Its `knownLlmRoleLabels` is unused and the mobile editor has no model card, so on iOS `conviction_llm_model_id` is a plain text field with a humanized label. Linking happens on web, per spec §2.
3. **The iOS home-screen widget** (`widgets_bridge/widget_sync_service.dart`) will list a short put with its raw OCC symbol and a signed qty. It is out of spec scope.
4. **An approval that later fails at the broker** (SwingSignals `status: "failed"`) is not shown in the card, because the card leaves on the 2xx. The operator learns of it through plan B's notifications or the instance logs.
5. **Shapes this plan assumed and the interfaces doc does not pin:** the list envelope, the decision error codes, and the wheel payload (see the Contract addendum). Plan B must return them, or the named readers change.
