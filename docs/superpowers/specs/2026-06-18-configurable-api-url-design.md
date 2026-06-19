# Configurable API Base URL — Design

**Date:** 2026-06-18
**Status:** Approved (brainstorming complete)
**Author:** Pranav + Claude

## Summary

Remove the hardcoded backend URL from the mobile app. The app must ask the user
for their IntelliStock API base URL on first launch and let them change it later
in Settings to point at a different instance. No hardcoded backend domain may
appear anywhere in the repository.

## Goals

- Zero hardcoded backend domain in the codebase — the former default host must
  not appear in code OR docs.
- First-run **Connect** screen prompts for the API base URL before login.
- The URL is persisted and editable later in **Settings**.
- The base URL is applied at runtime (Dio, the iOS/Android home widget, and any
  consumer) — not a compile-time constant.

## Non-Goals

- No multi-instance switching/bookmarks — one active URL at a time.
- No change to backend auth or the `/health` endpoint.
- No new storage dependency — reuse `flutter_secure_storage`.

## Decisions (settled in brainstorming)

1. **Drop `--dart-define=API_URL` entirely.** The base URL is purely a stored
   runtime value. A fresh install with nothing saved always prompts. No env
   override, no hardcoded default.
2. **Validate format + test connection.** The Connect screen / Settings editor
   requires a syntactically valid `http(s)` URL and probes `GET {url}/health`
   before saving, with a **"Save anyway"** escape hatch when the probe fails
   (so a temporarily-down backend doesn't block configuration).
3. **Changing the URL clears the session.** A token issued by instance A is not
   valid on instance B, so saving a *changed* URL clears the stored token/user
   and routes back to login for the new instance.

## Current state (verified)

- `mobile/lib/core/network/api_config.dart:10` — the ONLY code reference:
  `String.fromEnvironment('API_URL', defaultValue: '<former hardcoded backend URL>')`.
- Consumers of `ApiConfig.baseUrl`: `api_client.dart:92` (Dio `BaseOptions.baseUrl`),
  `session.dart:35` (`_syncWidgetCreds` → home-widget `widget_api_base`),
  `settings_screen.dart:344` (read-only display).
- Storage: `flutter_secure_storage` via `secureStorageProvider` (`session.dart:110`).
  `SessionStore` (`ChangeNotifier`) is the router's `refreshListenable`.
- `main.dart` pre-reads storage and calls `sessionProvider.load()` before `runApp`.
- Router (`core/router/router.dart`) `redirect` gates auth + onboarding;
  `initialLocation: '/dashboard'`.
- Backend `GET /health` (`backend/api/main.py:821`) is unauthenticated, returns
  200 JSON — the connection-probe target.
- Doc references to scrub: `docs/superpowers/plans/2026-06-10-mobile-flutter-app.md:159`,
  `docs/superpowers/specs/2026-06-10-mobile-flutter-app-design.md:10,125,229`.

## Architecture

Mirror the existing `SessionStore` pattern.

```
main.dart  ──load()──▶  ApiBaseUrlStore (secure storage key 'api_base_url')
                              │  isConfigured / baseUrl  (ChangeNotifier)
        ┌─────────────────────┼───────────────────────────────┐
   apiClientProvider     router redirect gate            SessionStore._syncWidgetCreds
   (Dio baseUrl =        (!isConfigured → /connect)      (widget_api_base = store.baseUrl)
    store.baseUrl)
```

### Components

1. **`core/network/api_base_url.dart`** (new)
   - `String normalizeBaseUrl(String raw)` — pure: trim; return `''` if empty;
     strip a single trailing `/`; leave scheme untouched.
   - `bool isValidBaseUrl(String raw)` — pure: parses, requires scheme `http`/
     `https` and a non-empty host.
   - `class ApiBaseUrlStore extends ChangeNotifier` over `FlutterSecureStorage`:
     `String get baseUrl`, `bool get isConfigured => baseUrl.isNotEmpty`,
     `Future<void> load()`, `Future<void> set(String url)` (normalizes, persists
     under `api_base_url`, notifies).
   - `apiBaseUrlProvider = ChangeNotifierProvider<ApiBaseUrlStore>(...)` using
     `secureStorageProvider`.
2. **`ApiConfig`** — remove `baseUrl` and the `String.fromEnvironment` (delete the
   `--dart-define`). Keep `connectTimeout`/`receiveTimeout`.
3. **`apiClientProvider`** — `ref.watch(apiBaseUrlProvider)`; build Dio with
   `store.baseUrl`. Rebuilds (new Dio) when the URL changes.
4. **`session.dart`** — `_syncWidgetCreds` takes the base URL from the store
   (inject the store or read the provider) rather than `ApiConfig.baseUrl`.
5. **`main.dart`** — `await container.read(apiBaseUrlProvider).load();` before
   `runApp` (alongside the session load) so the first frame/redirect sees it.
6. **Router** — `refreshListenable: Listenable.merge([session, store])`; add to
   `redirect`: if `!store.isConfigured` and not already on `/connect`, return
   `/connect`. New `GoRoute('/connect', ConnectScreen)`. The connect gate
   precedes the auth gate.
7. **`features/connect/presentation/connect_screen.dart`** (new) — a `TextField`
   (prefilled with the current URL when editing), a "Test & Connect" primary
   action, inline validation, and a probe result. Flow: normalize → if invalid
   format, show inline error; else probe `GET {url}/health` on a throwaway Dio
   (2–3s timeout) → 200 ⇒ save + leave (`store.set`, then router redirects to
   login/dashboard); non-200 / connection error ⇒ show the failure with a
   "Save anyway" button that saves without the probe.
8. **Settings** (`settings_screen.dart:344`) — the API row becomes tappable,
   opening the same Connect UI in "edit" mode (prefilled). On save of a URL that
   differs from the current one: `store.set(newUrl)`, then `sessionProvider.clear()`
   (force re-login on the new instance); the router refresh routes to `/login`.
9. **Docs scrub** — replace the literal domains in the two historical design docs
   with a neutral placeholder (e.g. `https://your-instance.example.com`).

## Error / empty / loading

- Unconfigured ⇒ the `/connect` gate; the app is unreachable without a URL.
- Probe failure ⇒ non-fatal, "Save anyway".
- Invalid format ⇒ inline error, save blocked.
- Throwaway probe Dio is isolated, so a bad candidate URL never corrupts the live
  client.

## Testing

- **Unit** (`mobile/test/.../api_base_url_test.dart`): `normalizeBaseUrl`
  (trailing slash, whitespace, empty) and `isValidBaseUrl` (accept
  `https://x.com`, `http://1.2.3.4:8000`; reject ``, `ftp://x`, `notaurl`,
  `https://`).
- **Widget** (`connect_screen_test.dart`): invalid input shows the inline error
  and does not save; the throwaway-probe path is exercised with an injected
  client or skipped if it requires real network.
- Connection probe + first-run gate + settings edit verified on-device.

## Rollout

Mobile-only; ships via `cd mobile && scripts/deploy.sh 1`. On a device with an
existing install, the saved URL (if any) is absent after this change (new key),
so the Connect screen appears on next launch — expected. No backend change.

## Open items

- Confirm the exact field/section widget used by `settings_screen.dart` around
  line 344 so the API row's tap target matches the existing row style.
- Decide whether the Connect editor is a full screen (route) or a modal sheet;
  default to a route (`/connect`) reused for both first-run and edit.
