# Parity checklist — core (Wave 1, task 1C)

Ports `mobile/lib/{main,app}.dart`, `mobile/lib/core/**`, `mobile/lib/widgets_bridge/**`,
`mobile/lib/features/connect/**`, `features/instances/presentation/live_logs_panel.dart`,
`mobile/ios/Runner/AppDelegate.swift`, and the PortfolioWidget restyle.

Legend: `[x]` ported as-is · `[x] → native form: …` deliberately changed in form · `[ ]` open.

## main.dart / app.dart

- [x] Portrait only → native form: `UISupportedInterfaceOrientations` in `project.yml` (already set in Wave 0).
- [x] First frame reads `intellistock_token`, `biometric_lock_enabled`, `lock_timeout` synchronously; seed = enabled, locked = enabled && authed, timeout → `AppLock.seed(storage:isAuthenticated:)` in `AppServices.init`.
- [x] URL store loads before the session, so the session's widget mirror sees the right URL (`AppServices.init` order).
- [x] App title `IntelliStock` → native form: display name `Intellistock Mobile` (Info.plist, Wave 0).
- [x] Dark-only theme → native form: follows system Light/Dark (spec §1 assumption, `dark-mode.md › Best practices`).
- [x] Lock gate covers everything when `locked && authed`; signing out drops the gate → native form: `LockView` overlay in `RootView` (content kept alive underneath, hidden from hit-testing and VoiceOver) instead of replacing the navigator.
- [x] Global chatbot dock overlays the app, self-hidden when signed out → `ChatEntrySlot` renders `ChatbotDockView()` only while authenticated.

## core/network

- [x] `api_config.dart`: connect 15 s / receive 30 s (Wave 0 `ApiClient.makeSession`).
- [x] `api_client.dart` / `api_error.dart` (Wave 0, extend-only; untouched).
- [x] `normalizeBaseUrl`: trim; http(s)+host → `scheme://host[:port]`; otherwise strip trailing slashes.
- [x] `isValidBaseUrl`: normalized, http/https scheme, non-empty host.
- [x] `ApiBaseUrlStore`: key `api_base_url`; `baseUrl`, `isConfigured`, `load()` (normalizes), `set(_:)` (empty → delete key, else write normalized).
- [x] `dioProvider` rebuilds the client when the base URL changes; the session is read, not watched → `AppServices.apiClient` rebuilt from `ApiBaseUrlStore.onChange`, same `SessionStore` token source.
- [x] `SessionStore`: keys `intellistock_token`, `intellistock_user` (JSON); `token`, `user`, `isAuthenticated` (non-empty token), `hasCompletedOnboarding` (`user.has_completed_onboarding == true`), `username` (`username ?? name ?? 'User'`).
- [x] `load()`: reads token + user; undecodable or non-object user → nil; mirrors widget creds.
- [x] `setSession(token, user)`: writes token; user → write JSON or delete key; mirrors widget creds.
- [x] `setToken(token)`: no-op when empty or unchanged; persistence best-effort (never throws); mirrors widget creds.
- [x] `setUser(user)`: writes JSON (no widget mirror, as in Dart).
- [x] `clear()`: deletes both keys; mirrors (empty token) to the widget.
- [x] Widget creds: App Group `group.dev.pkrishna.intellistock`, keys `widget_api_base` (live URL) and `widget_token`, then reload `PortfolioWidget`; errors swallowed.
- [x] 401 → session cleared → gates return to Login (Wave 0 client + `RootView`).

## core/router

- [x] Gate order: no URL → Connect; signed out → Login(redirect); onboarding incomplete → Onboarding; else shell → `AppGate.resolve`.
- [x] `?redirect=` safety: starts with `/`, not `//`, no `:`, no `@` → `AppGate.safeRedirect`.
- [x] Redirect honoured only when onboarding is complete at sign-in; otherwise onboarding, redirect dropped.
- [x] Sign-out records the current location as the redirect (`/login?redirect=<loc>`) → `AppRouter.location`, stored in `AppServices.loginRedirect`; router reset to a fresh shell.
- [x] Initial location `/dashboard` → `AppRouter.tab = .dashboard`.
- [x] Shell tabs Dashboard / Kalshi / Instances / Strategies + More, icons `dashboard`, `sports_soccer`, `memory`, `schema`, `menu` → native form: `TabView` with `Tab`s, filled SF Symbols; More uses `ellipsis` (Apple's More-tab glyph) instead of the hamburger.
- [x] Tapping the current tab returns it to its root (`goBranch(initialLocation:)`) → native form: system TabView pop-to-root on re-tap.
- [x] Push registration once when the shell appears → `MainTabView.task { await services.push.enable() }`.
- [x] Detail routes pushed over the shell (no bottom bar) → native form: pushed inside the current tab's `NavigationStack`, tab bar stays (`tab-bars.md › Best practices`).
- [x] `ChatbotFabSlot` placeholder → `ChatEntrySlot` overlay hook + `ChatbotDockView` placeholder (owned by the chat agent).
- [x] More sheet → native form: a real fifth tab (`MoreTabView`), inset-grouped `List`.
- [x] More rows, same order and labels: Crypto (`currency_bitcoin`, /crypto), Backtests (`analytics`, /backtests), Brokerages (`account_balance`, /brokerages), Agent Runs (`smart_toy`, /agent-runs), Nexus Graph (`hub`, /nexus), Learning (`lightbulb`, /learning), Models (`psychology`, /models), Token Usage (`payments`, /token-usage), Settings (`settings`, /settings).
- [x] Account row: person avatar + `session.username`; logout icon → `session.clear()` (no confirmation, as in Dart) → native form: a "Sign Out" destructive row under the account row.
- [x] `PlaceholderScreen` (route_screens.dart) — Wave 0 equivalent kept until every feature lands.

## core/lock

- [x] `LockTimeout`: immediately (0 s) "Immediately", oneMinute (60 s) "1 minute", fiveMinutes (300 s) "5 minutes"; `toStorageString` = seconds; `fromStorageString` unknown/nil → immediately.
- [x] Keys `biometric_lock_enabled` ("true"/"false"), `lock_timeout`.
- [x] Lifecycle: inactive/background record `pausedAt`; active → lock when enabled, authenticated, `pausedAt` set and elapsed ≥ timeout (zero = always); `pausedAt` cleared → driven from `scenePhase` via `AppServices.scenePhaseChanged`.
- [x] Never locks when signed out (login stays reachable).
- [x] `shouldGate` = locked && authenticated.
- [x] `unlock()`: reason "Unlock IntelliStock to access your portfolio"; success → unlocked, `pausedAt` cleared.
- [x] `enable()`: `canCheck` else false; reason "Authenticate to enable biometric lock"; persists "true".
- [x] `disable()`: persists "false"; enabled = false, locked = false.
- [x] `releaseLock()`: unlocked, `pausedAt` cleared, preference kept.
- [x] `setTimeout(_:)`: persists seconds string.
- [x] `BiometricService`: `canCheck` = isDeviceSupported (`.deviceOwnerAuthentication`) && canCheckBiometrics (local_auth_darwin rules incl. not-enrolled); `availableTypes` face/fingerprint; `authenticate(reason)` with `.deviceOwnerAuthentication` (`biometricOnly: false`); errors → false.
- [x] LockScreen auto-prompts on appear and again only when returning from a real background (not `inactive`).
- [x] Method label "Face ID" / "Touch ID" / "biometrics" from available types, optimistic "Face ID" default.
- [x] Headline: "Authenticating…" / "<method> wasn’t recognized." / "IntelliStock is locked."
- [x] Subtitle: "Hold still." / "Try again." / "Verify your identity to continue."
- [x] Button "Login with <method>" with Face ID or fingerprint glyph; dims while busy → native form: `Image(systemName: "faceid"/"touchid")`, `.glassProminent` capsule (`AuthPillButton`).
- [x] "Log out" appears only after a failure; disabled while busy; releases the lock then clears the session.
- [x] `_GlowingMark` (violet rim + bloom) → native form: app logo tile, no glow, no coloured shadow.

## core/polling

- [x] `IntervalPoller`: interval re-read each cycle; caller does the first fetch; errors never stop the loop; `pause` cancels the pending tick; `resume` schedules a fresh full interval; `dispose` stops → `PollingLoop`.
- [x] `AppLifecycleNotifier` (`isForeground`, `lastPausedAt` on paused/inactive) → `AppLifecycle`, fed from `scenePhase`.
- [x] `PollingNotifier` pauses in the background and resumes on foreground → `PollingLoop.run(lifecycle:)`, used from `.task {}` (cancels with the view).
- [x] `LogTailer`: path from cursor; 5 s running / 15 s idle (running = status nil/running/building); immediate re-poll on `truncated`; backoff 2/5/10/30 s; 10 000-line cap; new build id reseeds lines and cursor; `copyWith` null-keeps-previous semantics; `pause`/`resume` (resume polls now)/`dispose`.
- [x] `parseLogLine`: `[ts] message` with dotAll; ts via Dart `DateTime.tryParse`; classifier error/warn/success/info(broker)/normal, same keyword order.
- [x] `LogLine.color`: danger/warning/success/info/textMd → red/orange/green/blue/primary.

## core/push + AppDelegate

- [x] `PushDevice.fromJson` defaults (`device_token` "", `platform` "ios", `env` "prod"); `tokenSuffix` "…" + last 8 when longer than 8.
- [x] `GET /push/devices` → `devices` array (objects only); `POST /push/devices` body `device_token`, `platform: ios`, `env`, optional `app_version`; `DELETE /push/devices/{token}`.
- [x] `PushDevicesController` → `PushDevicesModel` (`Loadable<[PushDevice]>`, `refresh()` sets loading first).
- [x] `PushService.enable()`: idempotent; requests alert/badge/sound; registers for remote notifications when granted; errors swallowed.
- [x] Token → hex string → `POST /push/devices`, best-effort; env `sandbox` in DEBUG, `prod` otherwise.
- [x] App version → native form: sent from `CFBundleShortVersionString` (Dart called `enable()` without one, so `app_version` was omitted). Orchestrator decision.
- [x] `UNUserNotificationCenter` delegate set at launch; foreground banners `.banner, .sound, .badge`.
- [x] APNs registration failure logged (`NSLog`).
- [x] MethodChannel `intellistock/push` → native form: direct `AppDelegate` → `PushService` call.

## core/formatters

- [x] `fmtMoney` `$1,234.56` / `-$…` / `—`.
- [x] `fmtPnl` `+$…` / `-$…` / `—`.
- [x] `fmtPct` `+12.35%` (2 dp, + for ≥ 0, NaN → `—`).
- [x] `fmtUsdCost` 4 dp under $1 (non-zero), else 2 dp.
- [x] `fmtTokens` `1.2M` / `3.4k` / raw `num.toString()` (int "950", double "950.0").
- [x] `fmtDuration` `1.5s` / `3m 20s` / `2h 5m` / `1d 2h`.
- [x] `fmtElapsed` `Xd Xh Xm` / `Xh Xm Xs` / `Xm Xs` / `Xs`.
- [x] `parseDateTime`: Date, num (> 1e12 ms else s), numeric string, ISO string (Dart `DateTime.tryParse`), local.
- [x] `fmtDateTime` `MMM d, yyyy, h:mm a`; `fmtDate` `MMM d, yyyy` (en_US).
- [x] `fmtRelative` `Just now` / `Xm ago` / `Xh ago` / `Xd ago`, injectable now.
- [x] `pnlColor` success when ≥ 0 else danger → `Color`.
- [x] intl `NumberFormat` rounding (half away from zero on `fraction × 10ⁿ`) and Dart `toStringAsFixed` (round-half-up on the exact value) reproduced, not `printf`.

## core/charts

- [x] `fractionToIndex`, `indexToFraction`, `paddedBounds` (6 % pad, flat/empty cases), `valueToY`, `nearestIndexByTime`, `timeFractionOf`.
- [x] `ScrubController`/`ScrubSample`: tick only on index change; notifies on every distinct sample; `clear` notifies only when set.
- [x] `hourAmPm`, `formatChartDate(ts, range)`, `formatChartDateBySpan(ts, span)`, `evenlySpacedLabelIndices`.
- [x] `ChartDateLabels` row under the plot (first leading, last trailing).
- [x] `ScrubbableAreaChart` params: timestamps, values, lineColor, height (200), baseline, marker series, onScrub, animate, indexed, pulsingEndDot → native form: Swift Charts; markers are `ScrubbableChartMarker` points instead of Syncfusion series.
- [x] Empty or mismatched series → blank box of `height`.
- [x] Plot height = height − 20 (min 40); bounds include the baseline; hidden axes; monotone line, 2 pt.
- [x] Area fill → native form: flat `DS.chartAreaOpacity` fill, no gradient.
- [x] Scrub hairline + dot on the line at the snapped point; `onScrub(index)` on index change, `nil` on release → native form: `chartXSelection` + `RuleMark` + `PointMark`, `.sensoryFeedback(.selection)`.
- [x] Baseline reference line → `RuleMark(y:)`.
- [x] Pulsing end dot while not scrubbing → native form: solid dot with an expanding ring (no glow), static under Reduce Motion.
- [x] Entry animation once per view lifetime (700 ms) → native form: grow-in on first appearance, skipped under Reduce Motion or `animate: false`.
- [x] `hiddenValueAxis` / `edgeToEdge*Axis` (Syncfusion axis config) → native form: `chartXScale`/`chartYScale` domains with hidden axes (no Syncfusion).
- [x] `ScrubPainter` → native form: `RuleMark` + `PointMark` inside the chart.

## core/models (owned by the data agent)

- [x] `portfolio_history.dart`, `option_symbol.dart` → not written here (data agent owns `Core/Models/**`). `WidgetDataSyncer` parses the history fields it needs privately, mirroring `PortfolioHistory.fromJson`.

## core/theme + core/widgets

- [x] `AppColors` → `DS.Palette` + system semantic colours (spec §7).
- [x] `AppTextStyles` → Dynamic Type styles; `valueHero` → `.dsValueHero()` (40 pt bold scaled to `.largeTitle`, monospaced digits).
- [x] `app_background.dart` → native form: none; plain grouped background, no gradient, no glows.
- [x] `AppButton` primary/ghost/semantic → native form: `.borderedProminent` / `.borderless` / `.bordered` + tint (system button styles; no component).
- [x] `AppLogo(size, radius = size × 0.23)` → `AppLogoView`; `AppWordmark` "IntelliStock".
- [x] `AppToggle` → native form: `Toggle`.
- [x] `AuthPillButton(label, leading, showChevron, busy, busyLabel)` → `AuthPillButton`, `.glassProminent` capsule, large control size.
- [x] `BrokerageLogo(type, size 16, color = primary)` → asset `Brand/<type>` template-tinted; unknown/missing → `show_chart` (alpaca) or `savings`; decorative for VoiceOver.
- [x] `SectionHeader(title, eyebrow, subtitle, trailing)`.
- [x] `StatTile(label, value, valueColor, sub)`.
- [x] `AppBadge(label, color)` uppercase.
- [x] `LoadingState(label = 'Loading…')`.
- [x] `EmptyState(icon, title, subtitle, actionLabel, onAction)` → native form: `ContentUnavailableView` with a prominent action.
- [x] `ErrorBanner(message, onRetry)` + "Retry" → `ErrorRow`.
- [x] `IconTile(icon, color, size 40)` + `IconTile.custom`.
- [x] `showConfirmDialog(title, body, confirmLabel = 'Confirm', confirmColor = danger, icon, onConfirm)` → native form: `.confirmAlert(_:)` (`.alert` with Cancel and a role); the icon header is dropped; a failing `onConfirm` is reported through the request's `onError` instead of keeping a custom dialog open.
- [x] `FaceIdGlyph` → native form: `Image(systemName: "faceid")`.
- [x] `GlassCard` (plain / liquid / frosted, borderColor, onTap) → native form: `Card` on `secondarySystemGroupedBackground`, 22 pt continuous corners; no blur, no border, no shadow.
- [x] `material_symbols.dart` → `Symbol.named` (Wave 0).
- [x] `RelativeTimeText(timestamp, tick 20 s, clock)` → `TimelineView` re-render; nil → nothing.
- [x] `Skeleton` (shimmer) → native form: `.redacted(reason: .placeholder)`; `Skeleton` block kept as a flat fill for shapes with no sample content.
- [x] `StatusPill(label, color, pulsing)` → `StatusBadge` (caption2 semibold, 15 % fill, capsule, dot pulses unless Reduce Motion).
- [x] `StatusPill.colorForStatus` mapping → `StatusBadge.color(forStatus:)`; unknown → secondary (see rulings).
- [x] `TypedConfirmField(phrase, onMatchChanged, label = 'Type "<phrase>" to confirm')`, exact trimmed match, fires only on change → `TypedConfirmField` + `TypedConfirmMatcher`, plus `.typedConfirmAlert(_:)`.
- [x] SnackBar → native form: `Toast` + `.toast(_:)` (glass capsule, top, 2.5 s, announced to VoiceOver).
- [x] `flutter_markdown` → native form: `MarkdownText` block renderer (headings, paragraphs, lists, code, quotes, tables, rules).
- [x] `AsyncValue` → `Loadable<T>`.

## live_logs_panel.dart → `LiveLogsPanel`

- [x] Tailer path `/instances/{id}/live-logs?since_line={n}`, 5 s running / 15 s idle.
- [x] Collapsed by default; "View Live Logs" opens (resume + start), "Hide Logs" closes (pause).
- [x] Instance change → tailer rebuilt, state reset, panel closed.
- [x] Header: status dot (running → info, pulsing; failed → danger; else faint), `instance-<last 12 of build id or instance id>.log`.
- [x] Meta "<Running|Halted|Completed|Failed|Not running|Unknown> · N lines" when status present and not `none`.
- [x] "(last 500 — log file not available)" when `source == db`.
- [x] Pause/Resume and Copy only when open and lines exist; Pause ignored while loading.
- [x] Copy joins raw lines with `\n` → native form: `UIPasteboard` + "Copied" toast.
- [x] Search field "Search logs..." when lines exist; case-insensitive on raw text.
- [x] Loading row "Loading logs…"; empty copy "Waiting for log output…", "Broker not running. Start the instance to begin live trading logs.", "Can't reach logs endpoint. Retrying…".
- [x] Max height 400; sticks to the bottom unless scrolled up (48 pt threshold); "Jump to latest" button returns.
- [x] Row: timestamp `MM-dd, HH:mm:ss` column when parsed, message coloured by level.
- [x] Mono 9–11 pt → native form: `.caption2` monospaced (11 pt floor, HIG minimum).
- [x] Pauses while the app is in the background (see rulings).

## widgets_bridge

- [x] `IntradayPoint{t,v}`, `WidgetPortfolio`, `WidgetAccount` (+ `toPortfolioJson` with `asOf: ''`), `WidgetPosition`, `WidgetInstance`, `WidgetPayload` — same JSON keys and defaults.
- [x] `sync(payload)`: `portfolio_data`, `positions_data`, `instances_data`; reload `PortfolioWidget` and `InstanceWidget`.
- [x] `syncAccounts(accounts)`: `accounts_data`; first → `portfolio_data`; `synced_at` epoch seconds (int); reload `PortfolioWidget`.
- [x] Errors swallowed everywhere.
- [x] `WidgetDataSyncer.run()`: `GET /instances` → per instance `GET /instances/{id}/portfolio-history?range=1D`, skip empty; positions from `GET /instances/{id}/live-state` (best-effort); label = name or id; value = current or last; day P&L = change abs/pct; then `syncAccounts` when non-empty.

## features/connect

- [x] Title "Connect to your instance"; body "Enter the URL of your IntelliStock backend. You can change this later in Settings."
- [x] Field prefilled with the current URL, hint `https://your-instance.example.com`, URL keyboard, no autocorrect; submit → test & connect.
- [x] Invalid → "Enter a valid URL, e.g. https://your-instance.example.com".
- [x] Probe `GET {url}/health` (4 s timeouts, `Accept: application/json`), 200 = reachable.
- [x] Unreachable → "Couldn't reach {url}/health. Check the URL, or save anyway." and the button becomes "Save anyway".
- [x] Button "Test & Connect", busy while probing; editing the text clears the error and the "Save anyway" state.
- [x] Save: normalize; was configured and changed → clear session.
- [x] From Settings: changed → back to Login (fresh shell); unchanged → pop.
- [x] First run: no back affordance; the gate moves on to Login once a URL is set.
- [x] "Save Anyway" capitalisation → kept as "Save anyway" (sentence case in Dart); listed under copy.

## PortfolioWidget (restyle, data logic intact)

- [x] Reads `accounts_data`, `instances_data`, `synced_at`; self-fetch `GET {widget_api_base}/widget/accounts` with `widget_token`, caching `accounts_data` + `synced_at`.
- [x] Families small/medium/large/accessoryRectangular/accessoryInline; InstanceWidget small/medium.
- [x] Gradient background → native form: `.containerBackground(for: .widget)` with the system background; accented mode via `widgetAccentable`.
- [x] Curve gradient fill → native form: flat ≤ 15 % fill; dashed baseline.
- [x] Sub-11 pt text → native form: 11 pt floor (`widgets.md › Displaying text`).
- [x] Hard-coded dark palette → native form: semantic label colours + system green/red.
- [x] Custom 24–26 pt padding with content margins disabled → native form: system content margins.
- [x] `Text + Text` (deprecated in iOS 26) → string interpolation.

## Copy changes (title-style capitalisation, HIG `writing.md`)

- More tab title "More" (unchanged); account action "Sign Out" (Dart had an icon-only button).
- Connect button "Test & Connect" (unchanged), "Save anyway" kept as Dart wrote it (one-off escape; Apple uses sentence case for such secondary text actions — kept verbatim to avoid churn).
- LiveLogsPanel "View Live Logs" / "Hide Logs" (unchanged), "Jump to Latest" (was "Jump to latest").
- Lock "Log Out" (was "Log out").

## Rulings

See `.superpowers/sdd/2026-10-01-native-ios-port/task-1C-report.md` for the full list.
