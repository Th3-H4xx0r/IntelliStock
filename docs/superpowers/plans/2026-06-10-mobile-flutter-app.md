# IntelliStock Mobile (Flutter) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a native iOS/Android Flutter app that faithfully replicates the IntelliStock web UI (all 18 screens + chatbot), talks to the existing backend API, and adds a mobile Settings screen, Face ID app lock, and scaffolded iOS home-screen widgets.

**Architecture:** Feature-first Riverpod app. A shared `core/` provides the dark theme, glass-card design-system primitives, a dio `ApiClient` (JWT + error parsing), and generic `Poller`/`LogTailer` for the (websocket-free) realtime. Each feature owns `data/` (freezed models + repository), `application/` (providers), `presentation/` (screen). A `go_router` `StatefulShellRoute` drives a 5-tab bottom nav + "More" sheet, with full-screen detail routes over the shell.

**Tech Stack:** Flutter 3.44 / Dart 3.12, Riverpod 2 (+ generator), dio, freezed + json_serializable, go_router, Syncfusion Flutter Charts, flutter_markdown, flutter_secure_storage, local_auth, home_widget.

**Spec:** `docs/superpowers/specs/2026-06-10-mobile-flutter-app-design.md`

**Execution note:** Phase 1 (Foundation) is built first and defines every shared contract in §"Locked Contracts". Phases 2–6 are then implemented by **parallel subagents, one per feature**, because each feature is independent once the foundation and contracts exist. Each task lists exact files, responsibility, and acceptance/test criteria. Commit after each task.

---

## Locked Contracts (build in Phase 1, do not change afterward)

These signatures are shared by every feature. Parallel agents depend on them being stable.

### `core/theme/app_colors.dart`
```dart
abstract class AppColors {
  static const primary    = Color(0xFFA78BFA);
  static const onPrimary  = Color(0xFF04040C);
  static const canvas     = Color(0xFF04040C);
  static const surface    = Color(0xFF0F0A1C);
  static const border     = Color(0xFF231A3D);
  static const panel      = Color(0xFF0F1318); // modal/log panels
  static const panelAlt   = Color(0xFF0D1117);
  static const textHi     = Color(0xFFF1F5F9);
  static const textMd     = Color(0xFFCBD5E1);
  static const textMuted  = Color(0xFF94A3B8);
  static const textDim    = Color(0xFF64748B);
  static const textFaint  = Color(0xFF475569);
  static const success    = Color(0xFF34D399); // emerald-400
  static const danger     = Color(0xFFF87171); // red-400
  static const info       = Color(0xFF38BDF8); // sky-400
  static const warning    = Color(0xFFFBBF24); // amber-400
  static const teal       = Color(0xFF2DD4BF);
  static const chartUp    = Color(0xFF10B981);
  static const chartDown  = Color(0xFFEF4444);
  static const chartLine  = Color(0xFF38BDF8);
  static const chartGrid  = Color(0xFF1E2535);
  static const chartAxis  = Color(0xFF64748B);
  static Color fill(Color c)   => c.withOpacity(0.10); // semantic 10% bg
  static Color stroke(Color c) => c.withOpacity(0.20); // semantic 20% border
}
```

### `core/widgets/` primitive APIs
```dart
class GlassCard extends StatelessWidget {
  const GlassCard({this.child, this.padding = const EdgeInsets.all(20),
    this.onTap, this.borderRadius = 16});
}
class StatusPill extends StatelessWidget {
  const StatusPill({required this.label, required this.color, this.pulsing = false});
}
class AppButton extends StatelessWidget {
  const AppButton.primary({required this.label, this.icon, this.onPressed, this.busy});
  const AppButton.ghost({...}); const AppButton.semantic({required this.color, ...});
}
Future<bool> showConfirmDialog(BuildContext c, {required String title,
  required String body, required String confirmLabel, Color confirmColor,
  IconData icon, Future<void> Function()? onConfirm});
class TypedConfirmField extends StatefulWidget { // enables confirm only when input==phrase
  const TypedConfirmField({required this.phrase, required this.onMatchChanged});
}
class StatTile / EmptyState / LoadingState / AppBadge / AppTextField / AppToggle / SectionHeader / AppBackground;
IconData symbol(String name); // material_symbols.dart name→IconData map
```

### `core/network/api_client.dart`
```dart
class ApiClient {
  ApiClient(this._dio, this._session);
  Future<T> get<T>(String path, {Map<String,dynamic>? query});
  Future<T> post<T>(String path, {Object? body, Map<String,dynamic>? query});
  Future<T> put<T>(String path, {Object? body});
  Future<T> patch<T>(String path, {Object? body});
  Future<T> delete<T>(String path, {Map<String,dynamic>? query});
}
// AuthInterceptor: adds Bearer + JSON content-type; on 401 -> session.clear() + redirect /login.
// ApiError: from DioException -> message via {detail} (String | [{msg}] | Object).
// Provider: final apiClientProvider = Provider<ApiClient>((ref) => ...);
```

### `core/polling/poller.dart` and `log_tailer.dart`
```dart
// Poller: a mixin/base for AsyncNotifier that schedules refresh on an interval,
// pauses on AppLifecycleState.paused, resumes on resumed, cancels on dispose.
abstract class PollingNotifier<T> extends AutoDisposeAsyncNotifier<T> {
  Duration interval();            // override; may read state to pick 3s vs 10s
  Future<T> fetch();              // override
}
// LogTailer: cursor poller.
class LogTailerState { List<LogLine> lines; int nextLine; int totalLines;
  bool truncated; String? finalStatus; String source; String? buildId; Object? error; }
class LogTailer { LogTailer(this.client, this.pathBuilder);
  Stream<LogTailerState> stream(); void pause(); void resume(); void dispose(); }
class LogLine { final DateTime? ts; final String message; final LogLevel level; }
```

### `core/formatters/` (exact web parity)
```dart
String fmtMoney(num? v);          // $1,234.56 ; negatives -$1,234.56 ; null -> "—"
String fmtPnl(num? v);            // +$1,234.56 / -$1,234.56 (sign before $)
String fmtPct(num? v);            // +12.34% / -5.00% ; null -> "—"
String fmtUsdCost(num? v);        // $0.00 ; <$1 -> 4dp $0.0000
String fmtTokens(num? v);         // 1.2M / 3.4k / raw
String fmtDuration(num? seconds); // "1.5s" / "3m 20s" / "2h 5m" / "Xd Xh Xm"
String fmtElapsed(num? seconds);  // "Xd Xh Xm" / "Xh Xm Xs" / "Xm Xs" / "Xs"
String fmtDateTime(dynamic v);    // epoch s|ms|ISO -> medium date + short time
Color pnlColor(num? v);           // >=0 success else danger
```

### `core/router/router.dart`
```dart
// goRouterProvider: StatefulShellRoute.indexedStack with branches
//   [dashboard, instances, backtests, strategies, more]
// fullscreen routes pushed at root: /instances/:id, /instances/:id/live,
//   /backtests/:id, /backtests/:id/playback, /strategies/:id, and More-destinations.
// redirect(): handles auth, onboarding, and lock gates (see AppLockController).
// AppShell wraps the shell branches with the bottom NavigationBar + chatbot FAB.
```

---

## Phase 1 — Foundation

### Task 1.1: Scaffold the Flutter project
**Files:** Create `mobile/` (via `flutter create`), `mobile/pubspec.yaml`, `mobile/analysis_options.yaml`, `mobile/.gitignore`.
- [ ] Run `cd mobile && flutter create . --org dev.pkrishna --project-name intellistock_mobile --platforms ios,android`
- [ ] Add deps: `flutter_riverpod riverpod_annotation dio freezed_annotation json_annotation go_router syncfusion_flutter_charts flutter_markdown flutter_secure_storage local_auth home_widget intl google_fonts`; dev: `build_runner riverpod_generator freezed json_serializable flutter_lints`.
- [ ] Set `analysis_options.yaml` to `flutter_lints` + `prefer_const`. Verify `flutter pub get` succeeds.
- [ ] Commit `chore(mobile): scaffold flutter project`.

### Task 1.2: Theme + colors + text styles
**Files:** Create `lib/core/theme/{app_colors,app_text_styles,app_theme}.dart`.
- [ ] Implement `AppColors` (Locked Contracts). Implement `AppTextStyles` (Inter scale). Build `ThemeData.dark()` override with these tokens, `NavigationBarTheme`, `InputDecorationTheme` (violet focus), `DialogTheme`.
- [ ] Test `test/theme_test.dart`: assert key tokens (`AppColors.primary == Color(0xFFA78BFA)`), and `appTheme.brightness == Brightness.dark`.
- [ ] Commit.

### Task 1.3: Formatters (TDD)
**Files:** Create `lib/core/formatters/*.dart`; Test `test/formatters_test.dart`.
- [ ] Write failing tests for each formatter with web-parity cases: `fmtPnl(1234.5)=="+$1,234.50"`, `fmtPnl(-12)=="-$12.00"`, `fmtPct(12.345)=="+12.35%"`, `fmtPct(null)=="—"`, `fmtTokens(1200000)=="1.2M"`, `fmtTokens(3400)=="3.4k"`, `fmtUsdCost(0.0004)=="$0.0004"`, `fmtUsdCost(2)=="$2.00"`, `fmtElapsed(3661)=="1h 1m 1s"`, `fmtDuration(95)=="1m 35s"`, `fmtDateTime(1700000000)` ≈ medium date.
- [ ] Run → fail. Implement. Run → pass. Commit.

### Task 1.4: Design-system widgets
**Files:** Create `lib/core/widgets/*.dart` (all primitives in Locked Contracts) + `material_symbols.dart`.
- [ ] Implement GlassCard (gradient + blur + border + radius 16), StatusPill (dot + label, `pulsing` animation), AppButton (3 variants + busy spinner), AppBackground (radial glows + vertical gradient Stack), ConfirmDialog, TypedConfirmField, StatTile, EmptyState, LoadingState, AppBadge, AppTextField, AppToggle, SectionHeader.
- [ ] `material_symbols.dart`: map ≥60 used glyph names → `IconData` (dashboard, account_balance, memory, analytics, smart_toy, hub, schema, psychology, payments, person, logout, menu, close, refresh, add, open_in_new, play_circle, play_arrow, pause, stop, stop_circle, pause_circle, link, tune, delete, delete_sweep, warning, error, settings, expand_more, history, chat, send, lock, visibility, visibility_off, arrow_forward, arrow_back, monitoring, trending_up, trending_down, show_chart, candlestick_chart, area_chart, search, savings, replay, emoji_events, score, gavel, check_circle, cancel, power_off, hourglass_empty, bar_chart, auto_awesome, newspaper, terminal, arrow_downward, arrow_upward, unfold_more, filter_alt_off, add_shopping_cart, database, monitoring).
- [ ] Widget tests `test/widgets_test.dart`: TypedConfirmField disables confirm until phrase matches; StatusPill renders label; ConfirmDialog shows/cancels.
- [ ] Commit.

### Task 1.5: Networking + auth session
**Files:** Create `lib/core/network/{api_config,api_client,auth_interceptor,api_error}.dart`, `lib/features/auth/data/session.dart` (token store via flutter_secure_storage).
- [ ] Implement per Locked Contracts. `ApiConfig.baseUrl` from `--dart-define=API_URL` defaulting to `https://intellistock-api.pkrishna.dev`.
- [ ] Tests `test/api_error_test.dart`: `{detail:"x"}`→"x", `{detail:[{msg:"a"},{msg:"b"}]}`→"a; b", `{detail:{...}}`→stringified; 401 triggers session clear callback.
- [ ] Commit.

### Task 1.6: Poller + LogTailer (TDD)
**Files:** Create `lib/core/polling/{poller,log_tailer}.dart`; Test `test/polling_test.dart`.
- [ ] Tests with `fakeAsync`: PollingNotifier refetches each interval; pauses on `paused`; stops on dispose. LogTailer: cursor advances monotonically, `truncated:true`→immediate re-poll, error→backoff `[2,5,10,30]`, 10k cap drops oldest, `parseLine("[2026-01-01 12:00:00] hi")` → ts + "hi" + level.
- [ ] Run→fail, implement, run→pass. Commit.

### Task 1.7: Router shell + bottom nav + More sheet
**Files:** Create `lib/core/router/{router,app_shell,more_sheet}.dart`, `lib/app.dart`, `lib/main.dart`.
- [ ] `goRouterProvider` with StatefulShellRoute (5 branches) + placeholder screens for every route so the app runs. AppShell = NavigationBar (Dashboard/Instances/Backtests/Strategies/More) + AppBackground + chatbot FAB slot. MoreSheet lists Brokerages, Agent Runs, Nexus, Models, Token Usage, Settings, Logout.
- [ ] `main.dart` wraps `ProviderScope` + `MaterialApp.router`. Verify `flutter run` boots to a (placeholder) Dashboard.
- [ ] Widget test: shell renders 5 nav destinations. Commit.

### Task 1.8: Shared models barrel
**Files:** Create `lib/features/*/data/models/*.dart` stubs OR a shared `lib/core/models/` for cross-feature types (PortfolioHistory, SymbolHistoricals, LogLine). Generate with build_runner.
- [ ] Define freezed `PortfolioHistory{timestamps,values,currentValue,openValue,changeAbs,changePct}`, `SymbolHistoricals`, `User{...,hasCompletedOnboarding}`. `dart run build_runner build`.
- [ ] Commit. **End of Phase 1 — foundation complete; Phases 2–6 may parallelize.**

---

## Phase 2 — Auth, first-run, home (parallelizable after P1)

### Task 2.1: Auth (Login + session + guard)
**Files:** `lib/features/auth/{data/auth_repository.dart, application/auth_controller.dart, presentation/login_screen.dart, presentation/particle_field.dart}`.
- [ ] `AuthRepository`: `login(u,p)→{access_token,user}` (`POST /auth/login`), `me()` (`GET /auth/me`). `authControllerProvider` holds auth state, persists token.
- [ ] `LoginScreen`: "Private Access Only" pill, "Welcome back." h1, glass-card form (username `person`, password `lock` + show/hide, Sign In violet w/ dark text + spinner), error banner. `ParticleField` CustomPainter (48 drifting nodes, violet lines <110px) background over the login gradient.
- [ ] Router redirect uses auth state. Tests: repository parses `{access_token,user}`; login form validates non-empty; on success routes to redirect or `/dashboard`.
- [ ] Commit.

### Task 2.2: Onboarding (7-step wizard)
**Files:** `lib/features/onboarding/{data/onboarding_repository.dart, application/onboarding_controller.dart, presentation/onboarding_screen.dart, presentation/steps/*.dart}`.
- [ ] Repository: `state` (`GET /onboarding/state`), `complete`, `reset`. `PageView`-based wizard with progress header (numbered circles + shimmer connectors), directional slide transitions. Steps: Welcome, About, Model (reuse `LlmConfigForm` from Phase 5 — for P2 use a thin inline version or stub then wire in P5), Brokerage (reuse Brokerages link flow), Instance, Connect (flow diagram + 2 selects + RH warning), Complete (ray-burst checkmark + count tiles). Back/Skip/Next footer.
- [ ] Router: authed + `!hasCompletedOnboarding` → `/onboarding`. Tests: controller advances/retreats; complete → `/dashboard`.
- [ ] Commit. *(Note: Model/Brokerage steps depend on Phase 5/2.4 widgets; build the shell + Welcome/About/Connect/Complete first, wire shared forms when available.)*

### Task 2.3: Dashboard
**Files:** `lib/features/dashboard/{data/dashboard_repository.dart, application/*, presentation/dashboard_screen.dart, presentation/service_card.dart}`, `lib/features/dashboard/presentation/portfolio_chart.dart`.
- [ ] Repository fetches `/status`, `/agent/control`, `/digest/control`, `/nexus/status` (10s poll) + `/brokerages` once. Welcome header; Portfolio section = grid of `PortfolioChart` cards (Syncfusion AreaSeries + pan scrubber, range tabs `1D 1W 1M 3M YTD 1Y ALL`, default 1M, `GET /brokerages/{id}/portfolio-history?range=`); Services section = 5 control cards (Price Engine, Discover, AI Backtest Agent, Daily Digest, Nexus) with StatusBadge + start/stop/pause controls posting the documented control endpoints; Re-run onboarding panel.
- [ ] Start-Agent modal (special-request textarea). Empty/loading/error states. Tests: service card maps status→control; PortfolioChart scrubber updates readout. Commit.

### Task 2.4: Brokerages
**Files:** `lib/features/brokerages/{data/brokerage_repository.dart, data/models/brokerage.dart, application/*, presentation/brokerages_screen.dart, presentation/link_brokerage_sheet.dart, presentation/alpaca_test_panel.dart}`.
- [ ] Repository: list (`/brokerages`→`{accounts}`), link (`POST`), edit (`PUT`), remove (`DELETE`), refresh, `test-alpaca`, `robinhood/accounts`, portfolio-history. Account-card grid (icon by type, status dot, details, actions). Tabbed link sheet (Alpaca | Robinhood), Alpaca paper toggle + data-feed select + diagnostic test popup (gates save), Robinhood 2-step wizard (creds → account radio list w/ equity/BP, managed disabled).
- [ ] freezed `Brokerage` model. Tests: model parse; RH wizard step transition; alpaca test gates save. Commit.

---

## Phase 3 — Instances & live trading (parallelizable after P1)

### Task 3.1: Instance models + repository
**Files:** `lib/features/instances/data/models/{instance,live_state}.dart`, `data/instance_repository.dart`.
- [ ] freezed `Instance`, `LiveState{tradingActive, account{equity,cash,buyingPower,...}, positions[], recentTrades[], lookback?}`. Repository wraps every `/instances/*` endpoint from the catalog. Tests: parse instance + live-state JSON (nullable-tolerant). Commit.

### Task 3.2: Instances list + Instance Detail
**Files:** `lib/features/instances/presentation/{instances_screen.dart, instance_detail_screen.dart}` + modals.
- [ ] Instances: filter pills (All/User/AI + counts), instance card grid (status pill, origin badge, strategy/brokerage, stock chips, action buttons: View/Backtest/Link-Strategy/Strategy/Brokerage/Start/Stop/Delete), New Instance modal (id, name, granularity pills, start toggle, brokerage select w/ RH warning, max usage, strategy select), Add Stock, Delete (+force), Create Backtest modals.
- [ ] Instance Detail (`max-w-6xl` → single column): header w/ Start/Stop + Live Trading + refresh; 3 info cards (Instance Info w/ uptime ticker, Brokerage + market-data source, Strategy); stocks; **backtests table → stacked cards on mobile** w/ sort + pagination + 3s progress poll; Clear-State modal (3 scopes + preview + typed instance-id confirm).
- [ ] Tests: filter counts; uptime ticker increments; clear-state requires id match. Commit.

### Task 3.3: Live Trading (centerpiece)
**Files:** `lib/features/live_trading/{application/live_state_notifier.dart, presentation/live_trading_screen.dart, presentation/equity_chart.dart, presentation/position_card.dart, presentation/manual_order_sheet.dart}`.
- [ ] Fullscreen route (no bottom bar). Header status chip (+ broker/container/feed warnings), Halt + Manual Order + fullscreen toggle. Lookback banner. Hero equity card: Syncfusion **area/line/candlestick** toggle, **sequential-index x-axis** (no weekend gaps), green/red trend color, range tabs, 4-stat row. Secondary stat cards (Cash/Buying Power/Total P&L). Recent Executions list. Active Positions (per-position mini charts + Close). Live log panel (Task 3.4). Command toast.
- [ ] Adaptive poll **3s active / 10s idle** (PollingNotifier). Manual Order sheet (symbol/side/type/qty-xor-notional/limit/TIF/extended-hours, client validation → toast), Halt modal (TypedConfirmField "HALT"), Close modal ("CLOSE {SYMBOL}"). Commands: `POST live-command` → poll `live-commands/{id}` 1s.
- [ ] Tests: rangeStats high/low/pct; qty-xor-notional validation; HALT gate. Commit.

### Task 3.4: Instance live logs (LogTailer UI)
**Files:** `lib/features/instances/presentation/live_logs_panel.dart`.
- [ ] GlassCard w/ terminal header (status dot, filename, line count, source/truncated notes), search, pause/copy/download, View/Hide toggle. Windowed `ListView` (virtual scroll >300), color-coded levels, timestamp gutter, scroll-to-latest FAB. Drives `LogTailer` on `/instances/{id}/live-logs?since_line=`.
- [ ] Test: filter resets scroll; level coloring. Commit.

---

## Phase 4 — Backtests & strategies (parallelizable after P1)

### Task 4.1: Backtest models + repository; Backtests list
**Files:** `lib/features/backtests/data/models/backtest.dart`, `data/backtest_repository.dart`, `presentation/backtests_screen.dart`.
- [ ] freezed `Backtest` (+ summary/status/graph-data/playback-data/llm-cost shapes). Repository wraps `/backtests/*`. Backtests list: **table→stacked cards on mobile**, sort (Date/P&L/P&L%), status badges, inline progress bars, pause/resume/stop confirm modals, pagination, 3s status poll. Tests: status color map; pagination ellipsis. Commit.

### Task 4.2: Backtest Detail
**Files:** `lib/features/backtests/presentation/backtest_detail_screen.dart` + sections.
- [ ] Breadcrumb + header (status badge + action cluster Pause/Resume/Stop/Rerun/Playback/Delete). `BacktestLLMPauseBanner`. Nexus lookback banner. 7 stat tiles (2-col mobile). AI Credits card (totals + by-model/call-site/provider). Collapsible Strategy info. Logs panel (LogTailer on `/logs`). Portfolio value chart (Syncfusion area + scrub + start annotation). P&L-per-stock cards. Per-ticker accordions (lazy line chart + buy/sell markers + trade table + paginated decision trace). Round-trip stats. 3s poll while non-terminal; llm-cost every ~10th tick.
- [ ] Tests: confirm-modal action descriptions; lazy chart mounts on expand. Commit.

### Task 4.3: Backtest Playback
**Files:** `lib/features/backtests/presentation/backtest_playback_screen.dart`.
- [ ] Fullscreen. Top header (back + LIVE chip + Current Date pill + play/pause/reset/speed). Two-pane → **stacked on mobile** (timeline above, chart+holdings below). Vertical stepper of event nodes (date/strategy/outcome/decision) w/ fadeInUp, auto-scroll. Portfolio area chart (fixed x-range, animated). Holdings grid. Speed cycle `[0.5,1,2,5,10]`, delay `1000/speed`, no seek slider. Tests: frame advance; speed cycle. Commit.

### Task 4.4: Strategies + Strategy Detail
**Files:** `lib/features/strategies/{data/models/strategy.dart, data/strategy_repository.dart, presentation/strategies_screen.dart, presentation/strategy_detail_screen.dart}`, `lib/features/strategies/strategy_config.dart` (port of `strategyConfig.js` helpers).
- [ ] Strategies: Top-5 rank cards (medal theming) + **table→stacked cards** w/ sort + pagination + per-page. Merges `/strategies` + `/agent/results` + `/agent/top5` + `/backtests/best-per-strategy`. Strategy Detail: header (agent-best amber), sub-strategy cards (phase badges + config grid via `strategy_config` humanizer), backtests table, "Backtest this strategy" modal (instance picker grouped + params + validation). Port `strategyConfig.js`: `humanizeStrategyConfigKey`, `STRATEGY_FIELD_META`, LLM provider/effort option lists, role-group helpers.
- [ ] Tests: rank theming; humanizer acronyms (LLM/ETF/RSI); backtest-modal validation. Commit.

---

## Phase 5 — Operator screens & chatbot (parallelizable after P1)

### Task 5.1: Agent Runs
**Files:** `lib/features/agent_runs/{data/*, presentation/agent_runs_screen.dart}`.
- [ ] Status pill + controls (Start/Pause/Unpause/Stop + scheduled-resume **countdown ring** CustomPainter + force-stop). Runs grouped by cycle (timestamp dividers), cards w/ stage steppers (P&L colored), final verdict, pagination, 5s poll. Start-Agent + Resume (preset chips) modals. Tests: countdown ring progress; status→control. Commit.

### Task 5.2: Nexus Graph
**Files:** `lib/features/nexus/{data/*, presentation/nexus_screen.dart, presentation/nexus_logs_panel.dart}`.
- [ ] Header controls (Start/Stop/Auto-update/Full-Rebuild/Delete-edges). Auto-update + bootstrap cards. Graph counts tiles. Built vs Building states (hero / progress + **vertical stepper** of phases). 5 modals (Start w/ phase checkboxes + 13F quarters, Auto-update, Full Rebuild w/ typed "confirm", Delete edges w/ live progress, Bootstrap dates). NexusLogs panel (LogTailer on `/nexus-graph-builds/latest/logs`). Poll 2s running/5s idle. **No network graph viz.** Tests: phase selection payload; built/building branch. Commit.

### Task 5.3: Models + LlmConfigForm + Codex
**Files:** `lib/features/models/{data/*, presentation/models_screen.dart, presentation/llm_config_form.dart, presentation/codex_setup_panel.dart}`.
- [ ] Models table→cards (name/provider/model/effort/key/created/actions: test-cli/edit/delete). Add/Edit sheet: name + `LlmConfigForm` + pricing overrides + `/llm/test` result panel. `LlmConfigForm`: provider select + provider-conditional blocks (CLI path/args, Ollama base+model picker via `/ollama/list-models`, Bedrock region+picker via `/bedrock/list-models`, Azure endpoint/version, OpenAI/NVIDIA base URL, reasoning-effort). `CodexCliSetupPanel`: status + install (poll `install/{jobId}` 1.5s) + device-code login (poll 2s) + logout. Tests: provider-conditional field visibility; model create payload. Commit.

### Task 5.4: Token Usage
**Files:** `lib/features/token_usage/{data/*, presentation/token_usage_screen.dart}`.
- [ ] Health pill + 24h/7d/30d toggle + refresh. 4 KPI cards. Spend-trend **Syncfusion stacked ColumnSeries** by provider. Two ranking tables→cards (by model / call-site). By-run table (clickable backtest rows). Recent-calls table (50) → JSON detail modal. 10s auto-refresh (pause on background). Tests: fmtUSD/fmtTokens; range toggle refetch. Commit.

### Task 5.5: Chatbot dock
**Files:** `lib/features/chatbot/{data/*, application/chatbot_controller.dart, presentation/chatbot_dock.dart, presentation/chat_message.dart, presentation/chat_rich_block.dart, presentation/chat_tool_call.dart, presentation/chat_composer.dart, presentation/chat_model_picker.dart}`.
- [ ] FAB (pulsing) → bottom-sheet panel. Repository wraps `/chatbot/*`. Synchronous `turn` POST; tool-call confirm cards (`confirm-tool`); `navigate` blocks push routes; rich blocks (markdown via flutter_markdown + chart/table renderers); model picker; conversation switcher; clear; re-bootstrap on JWT change. Hidden on fullscreen routes. Tests: turn appends messages; pending_confirmation renders confirm UI. Commit.

---

## Phase 6 — Mobile-native (Settings, lock, widgets)

### Task 6.1: Settings screen
**Files:** `lib/features/settings/presentation/settings_screen.dart`, `lib/features/settings/application/settings_controller.dart`.
- [ ] Security (biometric toggle + auto-lock picker + require-on-launch), Account (username, Log out, Re-run onboarding → `POST /onboarding/reset`), About (version via `package_info_plus`, endpoint read-only, `showLicensePage`). Persist prefs in secure storage. Tests: toggle persists; reset routes to onboarding. Commit.

### Task 6.2: Biometric lock
**Files:** `lib/core/lock/{biometric_service,app_lock_controller,lock_screen}.dart`; modify `ios/Runner/Info.plist`, `android/app/src/main/AndroidManifest.xml`, `MainActivity` → `FlutterFragmentActivity`.
- [ ] `BiometricService` (local_auth). `AppLockController`: lock on launch (if enabled + session), re-lock on resume after timeout (`AppLifecycleState`), `enable()` requires successful auth first, graceful no-biometrics fallback. `LockScreen` (logo + Unlock w/ Face ID + passcode fallback + Log out). Router redirect: locked → `/lock`. `NSFaceIDUsageDescription`, `USE_BIOMETRIC`.
- [ ] Tests: enable-requires-auth; resume-after-timeout locks; resume-within-timeout doesn't; no-biometrics disables toggle. Commit.

### Task 6.3: iOS widget scaffolding
**Files:** `lib/widgets_bridge/{widget_payload,widget_sync_service}.dart`; iOS: `ios/PortfolioWidget/` (Swift WidgetKit extension), App Group entitlements on Runner + extension, `home_widget` registration.
- [ ] `WidgetPayload` (portfolio/positions/instances) + `WidgetSyncService.sync(...)` writing App-Group JSON + `HomeWidget.updateWidget`. Hook into dashboard/live-state fetch success. Create WidgetKit extension target with **stub** SwiftUI views for Portfolio (small/medium/large) + Instance-status families reading the App-Group JSON (placeholder rendering OK). Deep-link URL scheme → go_router. App Group `group.dev.pkrishna.intellistock`.
- [ ] Tests: `WidgetPayload` JSON round-trip; sync writes expected keys. Commit. **Note in commit body that full SwiftUI widget visuals are deferred per spec §13.**

---

## Phase 7 — Verify

### Task 7.1: Analyze + test + format
- [ ] `cd mobile && flutter analyze` → clean (or only intentional ignores). `flutter test` → green. `dart format lib test`.
- [ ] Commit any fixes.

---

## Self-Review (completed by author)

**Spec coverage:** Every spec section maps to a task — design system→1.2/1.4; nav→1.7; networking→1.5; polling→1.6; charts→2.3/3.3/4.x/5.4; all 18 screens→P2–P6; chatbot→5.5; settings→6.1; lock→6.2; widgets→6.3; testing→each task + 7.1. Landing/orb intentionally absent (dropped). ✓

**Placeholders:** Contracts are concrete; per-task acceptance/tests specified. Screen tasks reference the Locked Contracts rather than re-listing code (intentional for an app of this size — the contracts are the single source of truth). ✓

**Type consistency:** Shared names (`AppColors`, `GlassCard`, `StatusPill`, `TypedConfirmField`, `ApiClient`, `PollingNotifier`, `LogTailer`, formatter signatures) defined once in Locked Contracts and referenced thereafter. ✓

**Known sequencing:** Onboarding Model/Brokerage steps (2.2) reuse forms from 2.4/5.3 — build the wizard shell first, wire shared forms when ready.
