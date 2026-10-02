# Native iOS port of the IntelliStock mobile app — design

Date: 2026-10-01 · Branch: `feat/native-ios-app` · Status: approved to run autonomously ("Go")

## 1. What the operator asked for

The operator said:

- Port the Flutter app in `mobile/` to a pure native iOS app in a new `ios/` folder.
- Port **all** functionality "byte for byte".
- Give it an Apple-native look: Apple design elements, the HIG, the apple-design skill.
  Keep the content and behaviour the same and change only the style of the elements.
- Restyle while porting, so the work happens in fewer passes.
- When done, rename `mobile/` to `mobile_flutter_depricated` (their spelling, kept on purpose)
  and deprecate the Flutter app.
- Plan it with brainstorming, karpathy-guidelines, apple-design, and the ECC skills and agents.

I assumed the following. Each one can be reversed in a single place if the operator disagrees.

| Assumption | Why | How to reverse |
|---|---|---|
| SwiftUI, iOS 26.0 minimum, Swift 6 | The operator's iPhone 17 Pro Max runs iOS 27.0. iOS 26 brings native Liquid Glass. Xcode 27 and Swift 6.4 are installed | `deploymentTarget` in `ios/project.yml` |
| The app follows the system Light/Dark setting, with violet as the accent | `dark-mode.md › Best practices`: "Avoid offering an app-specific appearance setting… Ensure that your app looks good in both appearance modes." The Flutter app was dark-only | One line: `.preferredColorScheme(.dark)` on the root view |
| The bundle ID, App Group, team and URL scheme stay the same | The native app installs over the Flutter app. The keychain session, server URL, lock settings, push registration and home-screen widget all carry over | `ios/project.yml` |
| SF Pro and SF Mono replace Inter and JetBrains Mono | System type supports Dynamic Type and optical sizes (`typography.md`) | `DesignSystem/Typography.swift` |
| Android support ends with the Flutter app | The operator asked for "pure iOS" | n/a |
| No third-party dependencies | URLSession, Swift Charts, RealityKit, WidgetKit, LocalAuthentication and Foundation's Markdown cover everything | n/a |

## 2. Goals and success criteria

1. **Parity.** Every screen, route, control, action, endpoint call, request body, polling cadence,
   persisted key, error string, empty state and confirmation in `mobile/lib` has a native equivalent.
   Each feature carries a written parity checklist (§9), and every item on it is checked off.
2. **Apple-native style.** System navigation (TabView, NavigationStack, toolbars, sheets), SF Symbols,
   semantic colours, Dynamic Type, Swift Charts, and Liquid Glass only on the floating functional layer.
3. **Continuity.** A device that already runs the Flutter build opens the native build still signed in
   and pointed at the same server, with the same lock settings and pinned instances. No re-login.
4. **Verified.** The suite runs clean with `xcodebuild build test`, and the ported Dart unit tests pass
   as Swift Testing tests. A simulator screenshot tour logs into the real backend and visits every
   route. An ECC review sweep finds nothing outstanding.
5. **Deprecated.** `mobile/` becomes `mobile_flutter_depricated/` with a DEPRECATED note.
   `.gitignore` and the deploy tooling point at `ios/`.

Out of scope:

- New features.
- Backend changes.
- Android.
- Fixes to backend-side behaviour the Flutter app already copes with. Where a Flutter workaround
  exists, the port mirrors it.

## 3. Approaches considered

| | Approach | Verdict |
|---|---|---|
| **A** | **SwiftUI + Observation + async/await, one app target, XcodeGen with synced folders** | **Chosen.** It is the platform's current idiom: HIG components come for free and Liquid Glass applies automatically. Synced folders mean adding a file never touches `project.pbxproj`, so ten parallel worktrees merge without conflicts (verified with XcodeGen 2.46 on 2026-10-01) |
| B | UIKit with programmatic layout | Twice the code for the same screens, and it gets Liquid Glass and Dynamic Type only through extra work. Rejected |
| C | Wrap the existing Flutter views in native chrome | This is not a port, and the operator asked for Flutter to be deprecated. Rejected |

Within A, I also rejected:

- **A local Swift package for the core.** It would need a separate build graph for no benefit at this size.
- **Strict Codable models.** The Dart models parse leniently (`(j['x'] ?? 0)` and `as num?`), and strict
  decoding would turn tolerated backend quirks into crashes or empty screens. Models parse through a
  lenient `JSON` value instead (§5.3).

## 4. Project layout

```
ios/
  project.yml                    XcodeGen spec (source of truth)
  IntelliStock.xcodeproj/        generated, committed; synced folders keep it stable
  IntelliStock/                  app target (synced folder)
    App/                         IntelliStockApp, AppDelegate (push), RootView (gates), MainTabView, MoreTab
    Core/
      JSON/                      JSON lenient value + accessors
      Network/                   ApiClient, ApiError, ApiBaseUrlStore, SessionStore
      Storage/                   KeychainStore (flutter_secure_storage-compatible)
      Lock/                      AppLock, BiometricService, LockView
      Push/                      PushService, PushRepository, PushDevice
      Polling/                   PollingLoop, LogTailer, LogLine
      Formatters/                Formatters (ported 1:1)
      Models/                    shared models (PortfolioHistory, OptionSymbol, …)
      Navigation/                Route, AppRouter, RouteDestination
      WidgetBridge/              WidgetSync (App Group writer)
    DesignSystem/                tokens + components (§7)
    Features/<Feature>/          Data/ (models, repository) · Model/ (@Observable view models) · Views/
    Resources/                   Assets.xcassets (AppIcon, AccentColor, Brand/*.svg, AppLogo), coin.usdz
  PortfolioWidget/               WidgetKit extension (existing Swift, restyled)
  IntelliStockTests/             Swift Testing, ported from mobile/test
  IntelliStockUITests/           screenshot tour (XCUITest)
  parity/<feature>.md            parity checklists (§9)
  scripts/deploy.sh              build + install to the operator's iPhone
```

Build settings, applied to every target:

- `SWIFT_VERSION 6.0`, `SWIFT_DEFAULT_ACTOR_ISOLATION MainActor`, `SWIFT_APPROACHABLE_CONCURRENCY YES`.
  UI and view-model code is main-actor by default. Networking and parsing are `nonisolated` or
  `Sendable` where they leave the main actor.
- Deployment target `26.0`, `TARGETED_DEVICE_FAMILY 1` (iPhone only), portrait only (parity).
- `DEVELOPMENT_TEAM VY5CNF8734`.
- App bundle `dev.pkrishna.intellistockMobile`. Widget bundle `dev.pkrishna.intellistockMobile.PortfolioWidget`.
- App Group `group.dev.pkrishna.intellistock`, `aps-environment development`, background mode
  `remote-notification`.
- URL scheme `intellistock`.
- `NSFaceIDUsageDescription` keeps the Flutter text.
- Display name `Intellistock Mobile` (parity).

The commands everyone uses:

```sh
cd ios && xcodegen generate
xcodebuild -project ios/IntelliStock.xcodeproj -scheme IntelliStock \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro,OS=26.5' \
  -derivedDataPath <per-worktree path> build test
```

## 5. Core contracts (every feature codes against these)

### 5.1 Persistence continuity

`KeychainStore` reads and writes generic-password items with:

- `kSecAttrService = "flutter_secure_storage_service"`;
- `kSecAttrAccount = <key>`;
- UTF-8 values.

These are the attributes `flutter_secure_storage` 9.x uses on iOS. The Wave 1 core agent verifies them
against the plugin source in `~/.pub-cache` before shipping. The keys and value formats stay
**identical**:

| Key | Value |
|---|---|
| `intellistock_token` | JWT |
| `intellistock_user` | JSON object |
| `api_base_url` | normalised origin |
| `biometric_lock_enabled` | `"true"`/`"false"` |
| `lock_timeout` | seconds as a string (`0`/`60`/`300`) |
| `dashboard_selected_account` | as written by `selected_account_controller.dart` |
| `pinned_instances` | JSON array string |

The App Group `UserDefaults(suiteName: "group.dev.pkrishna.intellistock")` keys keep their names:

- `widget_api_base` and `widget_token`;
- `portfolio_data`, `positions_data`, `instances_data` and `accounts_data`;
- `synced_at`.

After a write, the app calls `WidgetCenter.shared.reloadTimelines(ofKind: "PortfolioWidget")` and
`(ofKind: "InstanceWidget")`.

### 5.2 Networking

`ApiClient` uses async URLSession and mirrors `api_client.dart` exactly:

- **Request setup:**
  - Base URL comes from `ApiBaseUrlStore`; the client is rebuilt when it changes.
  - Every request sends `Accept: application/json` and `Authorization: Bearer <token>` when a token exists.
  - POST, PUT and PATCH also send `Content-Type: application/json`.
  - Query parameters are encoded the way Dio encodes them.
- **Token handling:**
  - When a response carries an `x-refreshed-token` header, the client calls `session.setToken`
    without awaiting it.
  - On a 401, the client calls `session.clear()`.
- **Errors:** the client throws `ApiError(message, statusCode)`. The message comes from FastAPI's
  `detail` when that is a string, or from a list of `msg`/`message` entries joined with `"; "`. The
  transport-failure messages are identical, for example "Request timed out. Check your connection and
  try again." and "Cannot reach the server. Check your connection."
- **Timeouts:** connect 15 s, receive 30 s.
- **API:** `get/post/put/patch/delete(path, query:, body:) async throws -> JSON`.

### 5.3 JSON

`enum JSON` has the cases null, bool, number(Double), string, array and object. Its accessors coerce
the way the Dart code does:

| Accessor | Dart equivalent |
|---|---|
| `j["k"]` | map lookup; returns `.null` when missing |
| `.string` | `x?.toString()` |
| `.double` / `.int` | `(x as num?)?.toDouble()` |
| `.bool` | `x == true` |
| `.array` / `.object` | the collection, if the value is one |
| `.stringOr(_)`, `.doubleOr(_)` | defaults |

`num.toString()` formatting stays faithful. Integers print with no `.0`, so `5`, not `5.0`.

Every model has an `init(json: JSON)` that mirrors its Dart `fromJson` line for line, with the same
fallbacks. Request bodies are `JSON` literals with the same keys as the Dart maps.

### 5.4 State, polling, navigation

- **View models.** Each Riverpod notifier becomes one `@Observable final class` with the same name and
  `Controller` replaced by `Model`. Lifetime follows the original:
  - `autoDispose` providers: the screen owns the model in `@State`.
  - `keepAlive` providers: the model lives in `AppServices` for the whole app.
  - `AsyncValue` maps to `Loadable<T>` (`.loading`, `.loaded(T)`, `.failed(Error)`). Data stays
    visible during a refresh wherever the Dart screen keeps it.
- **Services.** `AppServices` is `@Observable` and injected with `.environment()`. It owns:
  - `ApiBaseUrlStore`, `SessionStore` and `AppLock`;
  - `ApiClient`;
  - every repository, as lazy properties;
  - the shared keepAlive models.
- **Polling.** `PollingLoop` ports `IntervalPoller` and `PollingNotifier`:
  - the caller performs the first fetch;
  - the interval is re-read every cycle;
  - an error never stops the loop;
  - polling pauses when `scenePhase != .active` and resumes on `.active`.
  - Use it as `.task { await model.poll() }`, which cancels when the view disappears.
- **Log tailer.** `LogTailer` ports cursor-paged `since_line` tailing:
  - 5 s while running, 15 s while idle;
  - an immediate re-poll on `truncated`;
  - backoff of 2/5/10/30 s after errors;
  - a cap of 10 000 lines;
  - the same line classifier.
- **Navigation.** `AppRouter` is `@Observable` and holds the selected tab plus one `NavigationPath` per
  tab. `Route` is one `Hashable` enum case per go_router path (§6). Each tab's `NavigationStack`
  resolves routes through a single `RouteDestination` switch. Placeholders exist from Wave 0, so every
  feature screen compiles before it is ported.

## 6. Navigation map

The gates mirror `router.dart` in order:

1. With no server URL, the app shows `ConnectView`.
2. When signed out, it shows `LoginView(redirectPath:)`, with the same `?redirect=` safety rules.
3. When onboarding is incomplete, it shows `OnboardingView`.
4. Otherwise it shows `MainTabView`.

`LockView` covers everything while `locked && authenticated`. The chat entry floats over the signed-in
app.

There are five tabs, using filled SF Symbols:

1. Dashboard
2. Kalshi
3. Instances
4. Strategies
5. **More**

In the Flutter app, More opened a sheet. Here it is a real tab with a list, because `tab-bars.md › Best
practices` says "Use a tab bar to support navigation, not to provide actions". It holds the same nine
destinations — Crypto, Backtests, Brokerages, Agent Runs, Nexus Graph, Learning, Models, Token Usage and
Settings — plus the account row with Sign Out.

| go_router path | `Route` case | Native view |
|---|---|---|
| /backtests | `.backtests` | BacktestsView |
| /backtests/:id | `.backtest(id)` | BacktestDetailView |
| /backtests/:id/playback | `.backtestPlayback(id)` | BacktestPlaybackView |
| /kalshi/instances/:id | `.kalshiInstance(id)` | KalshiInstanceDetailView |
| /kalshi/instances/:id/backtest | `.kalshiBacktest(instanceId)` | KalshiBacktestView |
| /kalshi/backtests/:id | `.kalshiBacktestResult(id)` | KalshiBacktestResultView |
| /instances/:id | `.instance(id)` | InstanceDetailView |
| /instances/:id/live | `.liveTrading(instanceId)` | LiveTradingView |
| /strategies/:id | `.strategy(id)` | StrategyDetailView |
| /brokerages | `.brokerages` | BrokeragesView |
| /crypto | `.crypto` | CryptoView |
| /crypto/instances/:id | `.cryptoInstance(id)` | CryptoInstanceDetailView |
| /search | `.search` | SymbolSearchView |
| /stock/:symbol (+StockScreenArgs) | `.stock(StockRoute)` | StockView |
| /agent-runs | `.agentRuns` | AgentRunsView |
| /nexus | `.nexus` | NexusView |
| /learning | `.learning` | LearningView |
| /models | `.models` | ModelsView |
| /token-usage | `.tokenUsage` | TokenUsageView |
| /settings | `.settings` | SettingsView |
| /settings/notifications | `.notificationSettings` | NotificationSettingsView |

A detail route pushes onto the current tab's stack, and the tab bar stays visible
(`tab-bars.md › Best practices`). In Flutter, details covered the shell. In-app links, chat-tool
navigation and widget deep links (`intellistock://…`) all go through `AppRouter.open(path:)`, which
parses the same path strings.

## 7. Design system: Flutter element → Apple element

| Flutter | Native | Note |
|---|---|---|
| `AppColors.canvas/panel/surface` | `.systemGroupedBackground` / `.secondarySystemGroupedBackground` / `.tertiarySystemGroupedBackground` | Semantic, adapts (`color.md`, `dark-mode.md`) |
| `textHi/Md/Muted/Dim/Faint` | `.primary` / `.primary` / `.secondary` / `.tertiary` / `.quaternary` | Label hierarchy |
| violet `primary` | `AccentColor` asset: light `#6D28D9` (7.1:1 on white, 6.4:1 on `#F2F2F7`), dark `#A78BFA` (7.7:1 on black, 6.3:1 on `#1C1C1E`) | One colour, one meaning: interactive and brand |
| success/danger/warning/info/teal | `.green` / `.red` / `.orange` / `.blue` / `.teal` | System colours, adaptive |
| `GlassCard` | `Card` container: `.background(.background.secondary, in: .rect(cornerRadius: 22, style: .continuous))`, or an inset-grouped `List` section where the content is rows | Glass stays OFF content (`liquid-glass.md`) |
| `AppBackground` gradients, gradient crowns, glows, blooms, shimmer | **None.** Every screen sits on the plain system grouped background. There are no gradients anywhere: backgrounds, cards, buttons, icons, the lock mark, chart fills. Charts draw a line with a flat ≤ 15 % area fill | The operator, 2026-10-01: "remove the gradients in the new app as well and make it look more apple UI like" |
| `AppButton` primary/secondary/danger/ghost | `.borderedProminent` / `.bordered` / `.borderedProminent` + `role: .destructive` / `.borderless`. Full-width form CTAs use `.controlSize(.large)` | `buttons.md` |
| `AuthPillButton`, chat FAB, floating actions | `.buttonStyle(.glassProminent)` / `.glass`, grouped in `GlassEffectContainer` | The functional layer only |
| `AppToggle` | `Toggle` | |
| `StatusPill` | `StatusBadge`: caption2 semibold, colour text on a 15 % fill, capsule | |
| `Skeleton` | `.redacted(reason: .placeholder)` over sample content | `loading.md` |
| `ConfirmDialog` | `.confirmationDialog` (destructive) or `.alert` with Cancel and a role | `alerts.md` |
| `TypedConfirmField` | `.alert` with a `TextField`; confirm is disabled until the text matches | |
| `showModalBottomSheet` | `.sheet` + `.presentationDetents` + a drag indicator | `sheets.md` |
| SnackBar | `Toast` capsule (glass, top, 2.5 s) for transient confirmations; inline error rows for failures | `feedback.md` |
| AppBar | `navigationTitle`: large on tab roots, inline on details. Actions are `ToolbarItem`s; overflow is a `Menu` (`ellipsis.circle`) | `toolbars.md` |
| Dropdown / PopupMenu | `Menu` / `Picker(.menu)` | |
| Segmented / choice chips | `Picker(.segmented)`, or `Menu` when there are more than 5 options | |
| Text fields | `Form`/`List` rows with `TextField`/`SecureField`, the right `keyboardType`/`textContentType` | `text-fields.md` |
| ExpansionTile | `DisclosureGroup` | |
| RefreshIndicator | `.refreshable` | |
| Spinners | `ProgressView` | |
| Haptics | `.sensoryFeedback` | |
| Clipboard | `UIPasteboard` + a "Copied" toast | |
| `url_launcher` | `@Environment(\.openURL)` | |
| `flutter_markdown` | `MarkdownText`: a block renderer over `AttributedString(markdown:, interpretedSyntax: .full)` | |
| syncfusion and custom-painter charts | Swift Charts. Scrubbing uses `chartXSelection` + `RuleMark` + selection haptics | `charts.md` |
| `ScrubbableAreaChart` | `ScrubbableAreaChart` on Swift Charts, with the same geometry rules (baseline, rebased 1D axis) | Ported helpers keep their tests |
| `Sector3DChart` | Swift Charts `SectorMark` donut with `chartAngleSelection`. The selected sector grows outward, the centre shows "Growth / NAME %", and a swipe steps sectors with a haptic | Same function, native form |
| login coin (`coin.glb`) | `coin.usdz`, converted with the system `usdcat`/`usdzip` and played in a RealityKit `RealityView` with the same four clips. Falls back to the static mark | The signature moment |
| odometer value | `.contentTransition(.numericText(value:))` | |
| Material Symbols | SF Symbols through `Symbol.named(_:)`. Every name in `material_symbols.dart` and every raw `Icons.*` use is mapped; unknown names fall back to `circle` | `sf-symbols.md` |
| Inter / JetBrains Mono | Dynamic Type text styles + `.monospacedDigit()`; logs use `.system(.caption, design: .monospaced)` | |
| `FaceIdGlyph` | `Image(systemName: "faceid")` / `"touchid"` | |
| brand SVGs | Asset-catalog SVGs with template rendering | |

Type scale. Flutter sizes are pt-equivalent, and the native scale moves up to HIG defaults:

| Flutter style | Native style |
|---|---|
| h1 24 | `.title2.bold()` |
| h2 20 | `.title3.bold()` |
| h3 16 | `.headline` |
| cardTitle 14 | `.subheadline.weight(.semibold)` |
| body 14 | `.body` in rows, `.subheadline` in dense cards |
| meta 12 | `.footnote` |
| micro 11 | `.caption` |
| nano 10 | `.caption2`, never below 11 pt |
| eyebrow | section header style |
| valueHero 38 | `.system(size: 40, weight: .bold)`, scaled relative to `.largeTitle`, plus `.monospacedDigit()` |

Signature and restraint:

- The coin on the login screen and the dashboard hero balance with numeric transitions are the two
  defining moments.
- Everything else is quiet system UI, in the manner of Apple's Stocks, Wallet and Settings apps:
  - inset-grouped lists;
  - solid secondary-background cards;
  - SF Symbols;
  - system controls with no custom chrome.
- **No gradients, glows, coloured shadows or decorative backgrounds anywhere.** This is the operator's
  explicit rule.
- Liquid Glass appears only on the system bars, the floating chat button and floating actions.

## 8. Work breakdown and ownership

**Wave 0 — kernel (orchestrator, inline).** It produces:

- `project.yml`;
- the targets;
- `JSON`, `ApiError` and `ApiClient`;
- `KeychainStore`;
- the DS tokens and `Symbol` map;
- `Route`, `AppRouter` and `RouteDestination`;
- a placeholder view for every route.

The project builds green before Wave 1 starts.

**Wave 1 — foundation, two agents in parallel worktrees.**

- **Core:**
  - all of `core/*`: session, URL store, lock and LockView, push and AppDelegate, polling, LogTailer and
    `LiveLogsPanel`, formatters, charts;
  - the remaining DS components;
  - the app shell: gates, tabs, the More tab, `ConnectView`, the chat entry slot;
  - `WidgetBridge`, and the `PortfolioWidget` port and restyle;
  - the core tests.
- **Data:**
  - every feature's `data/` layer: models and repositories;
  - the shared application helpers other features import: `dashboard_controller`,
    `portfolio_analytics`, `market_hours`;
  - the model and repository tests.

**Wave 2 — features, ten agents in parallel worktrees.** Each agent owns the `application/` and
`presentation/` layers of its features, plus their tests:

| Agent | Features | Dart LOC |
|---|---|---|
| auth | auth (login, coin), onboarding | ~3.7k |
| dashboard | dashboard (all sections, charts, insights, strategy cards, kalshi card), stock, symbol_search | ~8k |
| instances | instances (list, detail), swing | ~6.1k |
| live | live_trading (screen, equity chart, manual order, position card) | ~3.6k |
| backtests | backtests (list, detail, playback, LLM pause banner) | ~5.4k |
| kalshi | kalshi, crypto | ~6k |
| strategies | strategies (list, detail, strategy_config) | ~4k |
| models | models (LLM form, Claude/Codex setup), token_usage | ~5k |
| nexus | nexus, learning, agent_runs | ~6.7k |
| chat | chatbot (dock → glass button + sheet), brokerages, settings | ~6.8k |

**Wave 3 — integration.**

1. Merge the branches and build.
2. Run the full tests.
3. Run the simulator screenshot tour: an XCUITest that logs in with credentials from the environment
   and visits every route.
4. Run the parallel ECC sweep:
   - `ecc:swift-reviewer`;
   - `ecc:silent-failure-hunter`;
   - per-feature parity auditors that diff the Dart against the Swift;
   - an apple-design review of the screenshots.
5. Fix every finding.

**Wave 4 — deprecation.**

- `git mv mobile mobile_flutter_depricated`.
- Add `DEPRECATED.md`.
- Repoint the `.gitignore` rules.
- Add `ios/scripts/deploy.sh`.
- Run `gitnexus detect_changes`.
- Push the branch.
- **Never push to `main`.** A push there auto-deploys the backend and restarts real-money alpaca-main.

## 9. The parity process

Each Wave 1 and Wave 2 agent works in this order, before and during the port:

1. **Read every Dart file it owns in full.**
2. **Write `ios/parity/<area>.md`.** One checkbox per item:
   - screen, section and widget;
   - user action, with its endpoint, body and success/failure UI;
   - polling or timer cadence;
   - persisted key;
   - visible string: titles, labels, empty and error copy, confirmation text;
   - conditional state: loading, empty, error, disabled, role or kind gating.
3. **Port the application layer.** Each Dart test becomes a Swift Testing test. These tests port
   existing behaviour.
4. **Port the views, restyling in the same pass using §7.**
5. **Tick every checkbox.** Anything deliberately changed in form, such as More becoming a tab or the
   donut, gets a "→ native form: …" note. Nothing is dropped silently.
6. **Build and test green, then commit on the agent's branch.**

User-visible strings stay verbatim, except for capitalisation changes that HIG requires: title-style on
buttons and nav titles (`writing.md`). Each of those is listed in the checklist.

## 10. Testing

- **Unit tests.** Every logic test in `mobile/test` is ported to Swift Testing:
  - formatters, JSON parsing, `ApiError`, the auth interceptor rules, URL normalisation;
  - the lock controller state machine, polling, `LogTailer` parsing;
  - chart geometry and scrub logic;
  - each feature's model, repository and controller tests.
- **Dropped tests.** Widget and golden tests are not ported, because the visuals change by design.
  Their behavioural assertions move to view-model tests.
- **Repository tests.** These use a `URLProtocol` stub to assert the method, path, query and body, the
  same checks the Dart fakes made.
- **Screenshot tour.** The XCUITest (`IntelliStockUITests`) runs on the simulator against the live
  backend. Credentials come from `INTELLISTOCK_API_*`, passed through `TEST_RUNNER_` environment
  variables, and are never committed. Screenshots land in `ios/build/tour/`, which is gitignored.
- **Read-only during review.** The orchestrator reviews the screenshots against the apple-design
  checklist. The tour only navigates and never triggers trading actions.

## 11. Risks

| Risk | Mitigation |
|---|---|
| A keychain attribute mismatch logs the user out | Verify against the plugin source. The worst case is one re-login, with no data loss |
| The USDZ conversion loses the animation clips | Check in Wave 1 with `usdtree`/RealityKit. Fall back to a code-driven RealityKit rotation, and then to the static mark |
| Ten parallel builds thrash the CPU | Separate DerivedData per worktree. Agents build incrementally and test the touched suites before the full suite |
| Agents disagree on shared types | All shared types come from Waves 0 and 1 before Wave 2 starts. Wave 2 agents may not edit `Core/`, `DesignSystem/` or another feature's folder; they request changes in their report |
| A push to `main` deploys the backend | Only `feat/native-ios-app` is pushed |
