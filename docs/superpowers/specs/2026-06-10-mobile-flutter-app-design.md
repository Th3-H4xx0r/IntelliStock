# IntelliStock Mobile — Flutter App Design Spec

**Date:** 2026-06-10
**Status:** Approved (brainstorming complete)
**Author:** Pranav Krishna (with Claude)
**Branch:** `mobile-support`

## 1. Goal

Build a native iOS + Android app in Flutter that **faithfully replicates the existing IntelliStock web UI** (a Vue 3 + Vite + Tailwind + ApexCharts SPA in `frontend/`) and talks to the **same backend API** (`https://your-instance.example.com`). The app adds two mobile-only capabilities the web does not have: a **native Settings screen** and a **biometric (Face ID / Touch ID) app lock**, plus **iOS home-screen widget support** (scaffolded now, full WidgetKit rendering later).

The backend is **unchanged**; the app is a pure client of the existing REST API.

## 2. Scope

Full parity — all web screens are ported in one pass (built in phases, see §15):

Landing is intentionally **dropped** (mobile apps open to Login). The bespoke WebGL orb hero is not ported. Everything else is in scope.

**Screens (18) + chatbot dock:**
Login, Onboarding (7-step wizard), Dashboard, Brokerages, Instances, Instance Detail, **Live Trading** (centerpiece), Backtests, Backtest Detail, Backtest Playback, Strategies, Strategy Detail, Agent Runs, Nexus Graph, Models, Token Usage, **Settings (new)**, **Lock Screen (new)**, plus the global **Chatbot dock**.

## 3. Tech stack & key decisions

| Concern | Choice | Rationale / alternatives |
|---|---|---|
| Framework | Flutter 3.44 / Dart 3.12 (iOS + Android) | Installed and verified. |
| State mgmt | **Riverpod 2** (`riverpod_generator`, `hooks_riverpod` optional) | `AsyncNotifier` + auto-dispose fits the "fetch + poll + cancel" pattern on every screen. vs Bloc (boilerplate-heavy for ~20 polling screens), vs Provider/GetX (weaker async/testing). |
| Networking | **dio** + typed `ApiClient` + per-domain repositories | One interceptor handles JWT bearer, 401→logout, FastAPI `{detail}` error parsing. |
| Models | **freezed + json_serializable** | Immutable, null-safe data classes from the captured JSON contracts. |
| Charts | **Syncfusion Flutter Charts** (free Community license) | Live Trading needs area + line + **candlestick** toggling; Token Usage needs **stacked bars**. Syncfusion natively supports all four → closest parity to ApexCharts. fl_chart lacks candlestick. License note: free for <$1M rev / <5 devs (a personal project qualifies). |
| Routing | **go_router** | `StatefulShellRoute` for the bottom-nav shell + deep links for widget tap-through. |
| Markdown (chatbot) | **flutter_markdown** + custom rich-block renderers | Mirrors web's `marked` + `ChatRichBlock`. |
| Secure storage | **flutter_secure_storage** | JWT in Keychain/Keystore. |
| Biometric lock | **local_auth** | Face ID / Touch ID / Android BiometricPrompt. |
| iOS widgets | **home_widget** + native WidgetKit extension + App Group | Standard Flutter↔WidgetKit bridge. |
| Fonts/icons | **Inter** (bundled or google_fonts); Material Symbols → mapped to Flutter `Icons.*` | Web uses Inter + Material Symbols Outlined. |

## 4. Project location & structure

A new Flutter project lives in a new top-level dir `mobile/` inside this repo (backend unchanged).

```
mobile/
  lib/
    core/
      theme/        app_theme.dart, app_colors.dart, app_text_styles.dart
      widgets/      glass_card.dart, status_pill.dart, app_button.dart,
                    confirm_dialog.dart, typed_confirm_field.dart, stat_tile.dart,
                    empty_state.dart, loading_state.dart, app_background.dart,
                    section_header.dart, app_badge.dart, app_text_field.dart,
                    app_toggle.dart, material_symbols.dart
      network/      api_client.dart, auth_interceptor.dart, api_error.dart, api_config.dart
      polling/      poller.dart, log_tailer.dart
      formatters/   money.dart, percent.dart, tokens.dart, duration.dart, datetime.dart
      lock/         app_lock_controller.dart, lock_screen.dart, biometric_service.dart
      router/       router.dart, app_shell.dart, more_sheet.dart
    features/
      auth/         data/ application/ presentation/
      onboarding/   ...
      dashboard/    ...
      brokerages/   ...
      instances/    ...
      live_trading/ ...
      backtests/    ...
      strategies/   ...
      agent_runs/   ...
      nexus/        ...
      models/       ...
      token_usage/  ...
      settings/     ...
      chatbot/      ...
    widgets_bridge/ widget_sync_service.dart, widget_payload.dart
    app.dart  main.dart
  ios/PortfolioWidget/  (WidgetKit extension, App Group entitlement)
  test/  (unit + widget tests mirroring lib/)
  pubspec.yaml
```

Each feature folder is **data/** (freezed models + repository), **application/** (Riverpod providers/notifiers), **presentation/** (screen + screen-local widgets).

## 5. Design system → Flutter theme

Dark theme only (web hardcodes `<html class="dark">`; no toggle).

**Colors (`app_colors.dart`):**
- `primary #A78BFA`, `onPrimary #04040C` (dark text on violet buttons — load-bearing)
- scaffold/canvas `#04040C`, `surface #0F0A1C`, `border #231A3D`
- text: `#F1F5F9` (primary) / `#CBD5E1` / `#94A3B8` / `#64748B` / `#475569`
- semantic: success `#34D399`, danger `#F87171`, info `#38BDF8`, warning `#FBBF24`, teal `#2DD4BF`, accent-violet `#A78BFA`
- semantic recipe: **10% fill / 20% border / 400-level text**
- chart palette: up `#10B981`/`#34D399`, down `#EF4444`/`#F87171`, line `#38BDF8`/`#0EA5E9`, grid `#1E2535`, axis `#64748B`
- modal panel fills: `#0F1318` / `#0D1117` (GitHub-dark) acceptable next to `surface`

**`GlassCard`** (signature surface, used pervasively): vertical gradient `rgba(23,14,45,.78) → rgba(11,8,24,.70)`, 1px `rgba(188,154,255,.12)` border, 16px radius (`rounded-2xl`), `BackdropFilter` blur ≈14. A `.cardHover`-equivalent press state on mobile (ripple/scale).

**`AppBackground`** — `Stack` with two radial purple glows (top-left violet @14%, bottom-right magenta @10%) over a vertical gradient `#010107 → #02030a → #04040c`, behind every authenticated screen.

**Typography** — Inter (300–900). `tabular figures` for all numbers. Type scale: page H1 24/30 bold; eyebrow 12 uppercase tracking-widest primary; card title 14/16 semibold; body 14; meta 12; micro 10–11. Mono (system monospace) for IDs, tickers, timestamps, log lines, config values.

**Radii:** 6 / 8 / 12 / 16 (cards & modals = 16). **Easing:** `Curves.easeOutExpo` ≈ web's `cubic-bezier(0.16,1,0.3,1)`.

**Icon mapping** — a `material_symbols.dart` map from the web's Material Symbols names to Flutter `IconData` (e.g. `dashboard→Icons.dashboard`, `memory→Icons.memory`, `hub→Icons.hub`, `smart_toy→Icons.smart_toy`, `progress_activity→` a spinning indicator, etc.).

**Reusable primitives (1:1 with web patterns):**
- `StatusPill` — dot + label, pulsing dot when live; color by status (running=emerald, paused=violet, queued=amber, stopped=slate, error=red).
- `AppButton` — primary / ghost / semantic variants; disabled `opacity .4`; busy → spinner swap.
- `ConfirmDialog` — colored top border + icon header + title + body + Cancel/confirm; locks while busy; auto-dismiss on success.
- `TypedConfirmField` — the "type HALT / CLOSE {SYMBOL} / instance-id" safety gate. **Kept exactly** — confirm button disabled until the typed text matches.
- `StatTile`, `EmptyState` (big muted icon + headline + subtext + CTA), `LoadingState` (spinner + label), `AppBadge`, `AppTextField` (leading icon, violet focus), `AppToggle` (pill switch).

## 6. Navigation

- **`StatefulShellRoute.indexedStack`** with a bottom `NavigationBar`: **Dashboard · Instances · Backtests · Strategies · More**. Each tab keeps its own navigation stack.
- **"More" sheet** (modal bottom sheet from the More tab) → Brokerages, Agent Runs, Nexus Graph, Models, Token Usage, **Settings**, user info + **Logout** (the web sidebar footer).
- Full-screen detail routes push **over** the shell (no bottom bar): Instance Detail, **Live Trading**, Backtest Detail, **Backtest Playback** — matching the web's `fullscreenMode`.
- **Global chatbot FAB** floats above the shell on every authenticated tab; hidden on the fullscreen routes (matches web `!fullscreenMode`).
- Auth, Onboarding, and the **Lock Screen** render outside the shell.
- **Redirect guards** (mirror the web router): unauth → `/login?redirect=`; authed + onboarding incomplete → `/onboarding`; locked (biometric) → `/lock` gate before any authenticated content. On 401 anywhere → clear session → `/login`.

**Routes** (go_router): `/login`, `/onboarding`, `/lock`, shell tabs `/dashboard`, `/instances`, `/backtests`, `/strategies`, `/more`; `/instances/:id`, `/instances/:id/live`, `/backtests/:id`, `/backtests/:id/playback`, `/strategies/:id`, `/brokerages`, `/agent-runs`, `/nexus`, `/models`, `/token-usage`, `/settings`.

## 7. Data & networking layer

- **`ApiConfig`** — base URL `https://your-instance.example.com`; **bare paths** (the web's `/api` is a dev-proxy artifact; the backend serves `/auth/login` etc. directly). Overridable via `--dart-define=API_URL=`.
- **`ApiClient`** (dio) + **`AuthInterceptor`**: injects `Authorization: Bearer <token>`, `Content-Type: application/json` on writes; on 401 clears session and routes to `/login`; **no refresh** (matches web).
- **`ApiError`** — parses FastAPI `{detail}` as string | `[{msg}]` | object into a user-facing message.
- **freezed models** for: User, Instance, LiveState (account + positions + recent_trades), Strategy (+ sub-strategies), Backtest (+ summary, status, graph-data, playback-data, llm-cost), Brokerage, Model (LLM config), AgentRun (+ stages), NexusStatus (+ stages/counts/bootstrap), PortfolioHistory, SymbolHistoricals, LlmUsage (summary/timeseries/top-spenders/by-backtest/calls), Chatbot Conversation/Message/ToolCall, OnboardingState, EngineStatus.
- **One repository per domain** wrapping the full endpoint catalog (§7.1).

### 7.1 Endpoint catalog (bare paths, all Bearer unless noted)

- **Auth:** `POST /auth/login` (no auth) → `{access_token, user}`; `GET /auth/me`.
- **Onboarding:** `GET /onboarding/state`; `POST /onboarding/complete`; `POST /onboarding/reset`.
- **Instances:** `GET /instances`; `POST /instances`; `GET/PATCH /instances/{id}`; `POST /instances/{id}/start|stop|{action}`; `POST /instances/{id}/clear-state`; `POST /instances/{id}/link-brokerage|link-data-brokerage|link-strategy|unlink-strategy`; `POST /instances/{id}/stocks`; `DELETE /instances/{id}/stocks/{symbol}`; `GET /instances/{id}/live-state`; `POST /instances/{id}/live-command`; `GET /instances/{id}/live-logs?since_line=`; `GET /instances/{id}/portfolio-history?range=`.
- **Live cmd / market:** `GET /live-commands/{id}`; `GET /symbol-historicals?symbols=&range=`.
- **Strategies:** `GET /strategies`; `POST /strategies`; `GET /strategies/{id}[?force=true]`; `PUT /strategies/{id}`; `GET /strategies/available`.
- **Backtests:** `GET /backtests?page=&per_page=&sort_by=&sort_order=`; `POST /backtests`; `GET/DELETE /backtests/{id}`; `GET /backtests/{id}/status|summary|graph-data|playback-data|logs|llm-cost`; `POST /backtests/{id}/{action}`; `GET /backtests/best-per-strategy`.
- **Agent:** `GET /agent/runs?page=&per_page=`; `GET/POST /agent/control`; `POST /agent/runs/{logId}/force-stop`; `GET /agent/results?limit=`; `GET /agent/top5`; `GET /agent/best`.
- **Brokerages:** `GET /brokerages`; `POST /brokerages`; `PUT/DELETE /brokerages/{id}`; `POST /brokerages/{id}/refresh`; `GET /brokerages/{id}/portfolio-history?range=`; `POST /brokerages/test-alpaca`; `POST /brokerages/robinhood/accounts`.
- **Models:** `GET /models`; `POST /models`; `DELETE /models/{id}?force=true`; `POST /models/{id}/test-cli`; `POST /llm/test`; `POST /ollama/list-models`; `POST /bedrock/list-models`.
- **Codex CLI:** `GET /codex/status`; `POST /codex/install`; `GET /codex/install/{jobId}`; `POST /codex/login/start`; `POST /codex/logout`.
- **Nexus:** `GET /nexus/status`; `POST /nexus/control|rebuild|delete-edges|cache`; `GET /nexus-graph-builds/latest/logs?since_line=`.
- **Dashboard/services:** `GET /status`; `GET/POST /digest/control`; `POST /digest/send-now`; `POST /config/run-price-service|terminate-price`; `POST /discover/control`.
- **Token usage:** `GET /llm-usage/summary|timeseries|top-spenders|by-backtest|calls` (with `range`/`bucket`/`group_by`/`limit`).
- **Chatbot:** `GET/POST /chatbot/conversations`; `GET/PATCH/DELETE /chatbot/conversations/{id}`; `POST /chatbot/conversations/{id}/clear|turn|confirm-tool`; `GET /chatbot/tools`.
- **Misc:** `POST /benzinga/test`.

## 8. Real-time = polling (no websockets)

The web uses **zero** websockets/SSE — all live behavior is interval polling + cursor log tailing. Mirror exactly.

- **`Poller`** — Riverpod `AsyncNotifier` + `Timer.periodic` (or self-scheduling `Timer`), auto-cancels on dispose, **pauses on `AppLifecycleState.paused/inactive`** and resumes on `resumed`. Per-screen cadence: live trading **3s active / 10s idle**, dashboard **10s**, backtest status **3s** (stops on terminal), agent runs **5s**, token usage **10s** (pause on background), nexus **2s running / 5s idle**, live-command result **~1s** until terminal.
- **`LogTailer`** — cursor pattern: `GET …?since_line=N` → `{logs[], next_line, truncated, total_lines, final_status, source, id}`. Monotonic cursor, immediate re-poll on `truncated`, exponential backoff `[2,5,10,30]s` on error, in-memory cap 10k lines, source/build-swap reseeds from line 0. Cadence 2–5s running / 15s idle. UI: windowed `ListView` (virtual scroll for >300 lines), auto-stick to bottom unless scrolled up, scroll-to-latest FAB, in-memory search filter, pause/copy/download. Used by Instance live logs and Nexus build logs.

## 9. Charting (Syncfusion)

| Web chart | Syncfusion mapping |
|---|---|
| PortfolioChart / Dashboard area + scrubber | `SfCartesianChart` `AreaSeries` (gradient fill), datetime x-axis, custom **pan-gesture scrubber** overlay (binary-search nearest point, vertical line, value/change readout, reset on release) |
| Live Trading hero (area/line/candlestick toggle) | `AreaSeries` / `LineSeries` / `CandleSeries`; **sequential-index x-axis** (numeric category) to avoid weekend gaps; green/red trend coloring; range tabs `1D 1W 1M 3M YTD 1Y ALL`; 4-stat row |
| Per-position mini charts | sparkline-style `AreaSeries`/`LineSeries` (no axes) or small `CandleSeries`; `bucketCandles` synth |
| Backtest portfolio value | `AreaSeries` (sky) + scrub header + dashed "Start" annotation |
| Backtest per-stock price + trades | `LineSeries`/`AreaSeries` price + buy/sell **point markers** (emerald circle / red marker) |
| Backtest playback portfolio | `AreaSeries`, fixed x-range, animated |
| Token Usage spend trend | `SfCartesianChart` **stacked `ColumnSeries`** by provider, rounded columns, datetime x-axis, legend |

All charts: dark theme, transparent background, grid `#1E2535`, axis labels `#64748B`.

## 10. Chatbot dock

Global floating assistant (matches web `ChatbotDock`). FAB (violet, `smart_toy`, pulsing glow) → expanding panel (bottom-sheet on mobile, near-full-screen). Anatomy: header (icon + title + model name + fullscreen/settings/clear/minimise), conversation switcher, body (model picker on first run / empty state w/ suggestion pills / message list / settings), composer (auto-grow text field, send, Enter=send). Behaviors: **synchronous `turn` POST** (no streaming), tool-call **confirm cards** (`POST …/confirm-tool` with `{message_id, approved}`), `navigate` blocks push routes, rich blocks (charts/tables/markdown) via `flutter_markdown` + custom renderers, model selection, re-bootstrap on JWT change. State persisted locally.

## 11. Settings screen (new — More tab)

- **Security:** **Biometric Lock** toggle (Face ID/Touch ID/Android); **Auto-lock** picker (Immediately · 1 min · 5 min, default Immediately); "Require unlock on launch" (on when lock enabled).
- **Account:** username, **Log out**, **Re-run onboarding** (`POST /onboarding/reset` → `/onboarding`).
- **About:** app version, backend endpoint (read-only), open-source licenses (`showLicensePage`).
- No theme toggle (dark-only).

## 12. Biometric app lock (Face ID)

- **`BiometricService`** wraps `local_auth`: `canCheck()`, `availableTypes()`, `authenticate(reason)`.
- **`AppLockController`** (Riverpod) — holds `locked/unlocked`, `enabled`, `timeout`, `lastPausedAt`.
  - **On launch:** if `enabled` && session exists → render **`LockScreen`** before any authenticated content.
  - **On resume:** if `enabled` && `now - lastPausedAt > timeout` → re-lock.
  - **`LockScreen`** — branded dark screen (logo + "Unlock with Face ID" button), device-passcode fallback, retry, and a **Log out** escape hatch after repeated failures.
- **Enabling the toggle requires a successful biometric prompt first** (proves enrollment) before persisting `biometric_lock_enabled` + `lock_timeout` to `flutter_secure_storage`.
- **Graceful degradation:** if no biometrics enrolled/available → toggle disabled with an explanatory caption.
- Adds `NSFaceIDUsageDescription` to `Info.plist`; Android `USE_BIOMETRIC` permission + `FlutterFragmentActivity`.

## 13. iOS widget support (scaffolded now, rendered later)

- **`home_widget`** dependency + iOS **WidgetKit** extension target (`PortfolioWidget`) + **App Group** (`group.dev.pkrishna.intellistock`) + Keychain group.
- **`WidgetSyncService`** (Dart) — after each portfolio/live-state fetch, serialize a compact JSON `WidgetPayload` into the App Group container and call `HomeWidget.updateWidget`. Payload:
  - `portfolio`: `accountValue, dayPnlAbs, dayPnlPct, intradayPoints[] (t,v), asOf`
  - `positions[]`: `symbol, qty, marketValue, unrealizedPnlAbs, unrealizedPnlPct`
  - `instances[]`: `id, name, running, pnlAbs, pnlPct`
- **Widget families** (SwiftUI views **stubbed now**, full rendering later):
  1. **Portfolio** — *systemSmall*: big account value + day P&L (color-coded); *systemMedium*: + intraday equity line chart; *systemLarge*: + positions list each with P&L (the hybrid "expanded" widget).
  2. **Instance status** — running/stopped dot + name + live P&L.
- **Tap-through:** widgets deep-link via go_router (URL scheme) — e.g. portfolio → `/dashboard`, instance → `/instances/{id}/live`.
- Android home-screen widget parity is a later parallel (the `home_widget` Dart side is reusable).

## 14. Hard-to-port / dropped pieces

- **Landing page + WebGL orb:** dropped (app opens to Login). `AnimationView` (orb duplicate): dropped.
- **Login particle canvas:** kept — `CustomPainter` + `AnimationController` (48 drifting nodes, violet `rgba(167,139,250,.28)` dots, connecting lines under ~110px).
- **Tables that only h-scroll on web** (Strategies, Strategy-backtests, All-Backtests, Token-usage tables): re-laid out as **stacked cards** on phone (the top-5 grid + stat grids already reflow).

## 15. Build phases (for the implementation plan)

1. **Foundation** — scaffold `mobile/`, pubspec deps, theme + design-system primitives, `AppBackground`, formatters, `material_symbols` map, `ApiClient`/auth/interceptor/error, secure storage, router shell + bottom nav + More sheet, `Poller`/`LogTailer`. Unit tests for formatters + error parsing + poller/tailer.
2. **Auth & first-run & home** — Login (particle canvas), Onboarding (7-step), Dashboard (portfolio cards + service controls), Brokerages (tabbed link + Alpaca diagnostic + RH 2-step wizard).
3. **Instances & live** — Instances, Instance Detail, **Live Trading** (charts + scrubber + manual order/halt/close + typed-confirm), Instance live logs (LogTailer).
4. **Backtests & strategies** — Backtests, Backtest Detail (stats/llm-cost/logs/charts/decisions), Backtest Playback, Strategies, Strategy Detail.
5. **Operator & chatbot** — Agent Runs (countdown ring), Nexus (stepper + counts + logs), Models (+ LlmConfigForm + Codex device-code flow), Token Usage (stacked bar), Chatbot dock.
6. **Mobile-native** — Settings screen, biometric lock (`AppLockController` + `LockScreen`), iOS widget scaffolding (App Group, WidgetKit extension, `WidgetSyncService`, stub SwiftUI widgets, deep links).

## 16. Testing strategy

- **Unit:** formatters (exact `+$1,234.56` / `+12.34%` / `1.2M`/`3.4k` / `Xd Xh Xm` parity), `ApiError` parsing, `Poller` (cadence, pause-on-background, cancel-on-dispose), `LogTailer` (monotonic cursor, truncated re-poll, backoff, 10k cap), `WidgetSyncService` serialization, `AppLockController` (lock-on-resume-after-timeout, enable-requires-auth, no-biometrics fallback).
- **Widget tests:** GlassCard, StatusPill, ConfirmDialog, **TypedConfirmField** (confirm disabled until match), AppButton states, chart wrapper smoke.
- **Static:** `flutter analyze` clean; `dart format`.
- Backend's ~18 known-failing Python tests are a separate repo concern, untouched.

## 17. Assumptions & non-goals

- **Assumptions:** new `mobile/` dir in this repo; backend API unchanged; iOS-first for widgets; the live API is reachable at `https://your-instance.example.com`.
- **Non-goals (this pass):** marketing landing page, WebGL orb, Android home-screen widget, push notifications, offline caching/persistence beyond auth token, full SwiftUI widget visual polish (stubbed now).

## 18. Risks

- **Syncfusion license** — Community license assumed valid for this project; if not, fall back to fl_chart + a custom candlestick painter for Live Trading.
- **Chart parity** — the sequential-index x-axis trick and gradient/candle styling must be matched for visual fidelity; budgeted in Phase 3.
- **WidgetKit setup** — requires native Xcode target + entitlements; scaffolded in Phase 6, full rendering deferred.
- **API drift** — data shapes are *inferred* from the frontend; some fields may differ. Repositories tolerate missing/nullable fields (freezed defaults) and the app degrades gracefully.
