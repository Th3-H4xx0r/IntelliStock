# Session longevity + live widgets — design

Date: 2026-06-16
Status: approved (ready for implementation plan)

## Problem

Three reported issues, two of which share one root cause:

1. **Home/lock-screen widgets don't update at all.** They show stale data indefinitely.
2. **The widget "Updated …" label is permanently stuck on "Just now"** — it never reflects how long ago data actually refreshed.
3. **Users are logged out roughly every 24 hours** on both web and the mobile app. Login should last ~1 month.

### Root causes (verified in code)

- **24h JWT.** `backend/auth_utils.py:238` issues access tokens with `timedelta(hours=int(JWT_EXPIRE_HOURS, "24"))`. There is no refresh mechanism — on any 401 the mobile app wipes the session and forces re-login (`mobile/lib/core/network/api_client.dart` `AuthInterceptor.onError`).
- **Widgets share that JWT.** `mobile/lib/core/network/session.dart:36` mirrors the token into the App Group as `widget_token`. The widget self-fetches `/widget/accounts` with it (`PortfolioWidget.swift:102`). When the token expires, the fetch 401s and the widget silently keeps cached data → it freezes. **So issue #1 is primarily caused by issue #3.**
- **Static relative label.** The widget label is a plain string computed once at timeline-build time (`relativeAgo()` → static `Text`, `PortfolioWidget.swift:209` and `:230`) and the timeline has a single entry (`Timeline(entries: [e], policy: .after(next))`, `:486`). It never re-renders between reloads. Each successful self-fetch also resets `synced_at` to now (`:123`), so every reload re-prints "Just now."
- **iOS refresh budget.** WidgetKit budgets background timeline reloads (~40–70/day, effectively every ~15–30 min) and throttles further on usage. The current code floors the requested interval at 10 min (`:484`) precisely because sub-10-min requests burn the budget and cause *fewer* refreshes. A literal 5-min background refresh is not achievable through normal widget timelines.

## Approach (approved)

- **Session:** 30-day access token + transparent sliding renewal (chosen over a full refresh-token flow as right-sized for a single-operator app).
- **Widget refresh:** best-effort — fix the token (the real cause of "not updating"), keep the existing interval/floor logic, keep instant refresh on app foreground. Do **not** chase a literal 5-min cadence (would risk budget exhaustion → the silence already observed).
- **Label:** auto-updating relative text in the widget; a new live in-app freshness label on the dashboard.

## Part A — Session: 30-day token + sliding renewal

### Backend
File: `backend/auth_utils.py`
- `create_access_token`: default lifetime `24h → 720h` (30 days), still overridable via `JWT_EXPIRE_HOURS`. Add an `iat` (issued-at, UTC) claim to the payload alongside `exp`.

File: `backend/api/main.py`
- `get_current_user`: add a `response: Response` parameter. After successful validation, compute the token's lifetime from `iat`/`exp`; if `remaining < lifetime/2` (past half-life), mint a fresh 30-day token and set it on `response.headers["X-Refreshed-Token"]`. This runs on every authenticated request, so an active user's window slides forward continuously and never reaches expiry. Wrap in try/except so a renewal hiccup never breaks the request.
- CORS middleware (`:187`): add `expose_headers=["X-Refreshed-Token"]` so the web client can read the header. (Mobile/Dio does not require this.)

### Mobile
File: `mobile/lib/core/network/session.dart`
- Add `Future<void> setToken(String token)` to `SessionStore`: update in-memory `_token`, persist to secure storage (`_kToken`), call `_syncWidgetCreds()` (re-mirrors the fresh token to the widget), and `notifyListeners()`. Does not touch the cached user.

File: `mobile/lib/core/network/api_client.dart`
- `AuthInterceptor`: add `onResponse` that reads `X-Refreshed-Token`; if present, non-empty, and different from the current token, call `_session.setToken(newToken)`. The existing 401 → logout stays as the final fallback. The interceptor needs access to `SessionStore` (already injected).

### Web
Directory: `frontend/`
- Add a response interceptor in the web HTTP layer that stores `X-Refreshed-Token` (when present) wherever the auth token currently lives. The 30-day token alone already removes the daily web logout; this makes the web session slide too. Exact HTTP-client file to be identified during implementation.

### Migration behavior
Tokens issued before this deploy lack `iat`, so the half-life check is skipped for them — those users re-login **once** when their old 24h token finally expires, then receive the 30-day sliding token. No flag day, no forced mass logout.

### Tradeoff (accepted)
A 30-day bearer token has no server-side revocation; a leaked token is valid up to 30 days. Acceptable for a single-operator personal app. Sliding renewal rotates tokens, which is marginally better than one static long-lived token. Documented, not mitigated further in this pass.

## Part B — Widget refresh (best-effort)

File: `mobile/ios/PortfolioWidget/PortfolioWidget.swift`
- **No change to the interval/floor logic.** The existing `max(configuration.refresh.seconds, 600)` + `.after(next)` already passes the user's configured interval to iOS; the floor protects the refresh budget. Add/clarify a comment that sub-15-min cadence is not honored by iOS in the background and why.
- The real fix is Part A: with a live token, the existing per-reload `fetchAndCacheAccounts()` succeeds again and the widget resumes refreshing at roughly the configured cadence (as far as iOS allows).
- Instant foreground refresh already exists (`dashboard_screen.dart:33` `widgetSyncProvider` → `HomeWidget.updateWidget`) and is retained.

Out of scope (noted for later): true ~5-min freshness would require silent-push-triggered `WidgetCenter.reloadAllTimelines()` using the existing APNs infra.

## Part C — "Updated X ago" label

### Widget
File: `mobile/ios/PortfolioWidget/PortfolioWidget.swift`
- Replace the static label at both call sites (home overlay `:209`, lock-screen rectangular `:230`) with SwiftUI auto-updating relative text driven off `syncedAt`, e.g. `Text(Date(timeIntervalSince1970: entry.syncedAt), style: .relative)` composed with a " ago" suffix. iOS re-renders this on its own clock without a timeline reload, so it ticks "1m ago → 2m ago…" and honestly shows data age even between the ~15–30 min reloads. Preserve the existing guards (`entry.hasData`, `syncedAt > 0`). Verify sub-minute wording on-device.

### In-app (Flutter dashboard)
- New reusable widget `RelativeTimeText` (`mobile/lib/core/widgets/`): a `StatefulWidget` holding a periodic `Timer` (~20s) that re-renders `fmtRelative(timestamp)` (reusing `mobile/lib/core/formatters/formatters.dart`). Cancels the timer on dispose.
- New `portfolioUpdatedAtProvider` (`StateProvider<DateTime?>`, in the dashboard application layer): stamped to `DateTime.now()` by `_HistoryNotifier` on each **successful** fetch — both initial `_fetch` and poll `_refresh` (`mobile/lib/features/dashboard/presentation/portfolio_chart.dart`). Not updated on failure, so it reflects the last *successful* refresh.
- Render `RelativeTimeText` in `_PortfolioSection` (`dashboard_screen.dart`) near the hero. On the 1D live view (5s poll) it reads "Just now"/"5s ago" — accurate — and ticks up honestly if a poll stalls.

## Files touched

Backend (lands on `main`; user Dokploy-deploys):
- `backend/auth_utils.py` — `create_access_token` (lifetime default + `iat`).
- `backend/api/main.py` — `get_current_user` (sliding renewal header), CORS `expose_headers`.

Mobile (branch `feat/mobile-dashboard-ui`; deploy via `mobile/scripts/deploy.sh 1`):
- `mobile/lib/core/network/session.dart` — `setToken`.
- `mobile/lib/core/network/api_client.dart` — `AuthInterceptor.onResponse`.
- `mobile/ios/PortfolioWidget/PortfolioWidget.swift` — auto-updating relative label (×2), interval comment.
- `mobile/lib/core/widgets/relative_time_text.dart` — new `RelativeTimeText`.
- `mobile/lib/features/dashboard/application/…` — `portfolioUpdatedAtProvider`.
- `mobile/lib/features/dashboard/presentation/portfolio_chart.dart` — stamp updated-at on success.
- `mobile/lib/features/dashboard/presentation/dashboard_screen.dart` — render label.

Web (lands on `main` with backend):
- `frontend/` HTTP layer — capture `X-Refreshed-Token`.

## Implementation notes
- Per `CLAUDE.md`, run `gitnexus_impact` on `create_access_token` and `get_current_user` before editing and report blast radius. The GitNexus index is stale (backend symbols added since `9fd194b`) — reindex with `npx gitnexus analyze` first, or proceed with manual caller verification.

## Testing
- Backend unit tests: `create_access_token` includes `iat` and defaults to 720h (and honors `JWT_EXPIRE_HOURS`); half-life renewal logic in `get_current_user` (no `iat` → no renewal; past half-life → header set; fresh token → no header).
- Mobile tests: `AuthInterceptor` persists `X-Refreshed-Token` and re-syncs widget creds; `RelativeTimeText` ticks over time. Keep the existing 336 tests green; `flutter analyze` clean (except the 1 pre-existing doc-comment info).
- On-device: widget label ticks live; after a long background gap the widget refreshes and shows honest age now that the token is long-lived; confirm no daily logout.

## Risks
- Setting a response header from a FastAPI dependency on every authed request — low cost; confirm it doesn't interfere with streaming/file responses.
- Token churn when multiple in-flight requests cross half-life at once — harmless (all valid), bounded until the client adopts the new token.
