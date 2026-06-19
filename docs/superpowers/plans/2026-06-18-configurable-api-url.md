# Configurable API Base URL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the hardcoded backend URL; prompt for the API base URL on first launch and let the user change it in Settings, applied at runtime.

**Architecture:** A persisted `ApiBaseUrlStore` (`ChangeNotifier` over `flutter_secure_storage`, key `api_base_url`) mirrors the existing `SessionStore` pattern. Dio reads the URL at build and rebuilds on change; the router gates an unconfigured app to a `/connect` screen before login; Settings reuses `/connect` to edit. No compile-time URL, no `--dart-define`.

**Tech Stack:** Flutter / Dart / Riverpod / Dio / go_router / flutter_secure_storage. Spec: `docs/superpowers/specs/2026-06-18-configurable-api-url-design.md`.

---

## File Structure

- Create `mobile/lib/core/network/api_base_url.dart` — `normalizeBaseUrl`, `isValidBaseUrl` (pure), `ApiBaseUrlStore`, `apiBaseUrlProvider`.
- Modify `mobile/lib/core/network/api_config.dart` — drop `baseUrl` + the `--dart-define`; keep timeouts.
- Modify `mobile/lib/core/network/api_client.dart` — `dioProvider` watches `apiBaseUrlProvider`.
- Modify `mobile/lib/core/network/session.dart` — `_syncWidgetCreds` uses an injected base-URL getter.
- Modify `mobile/lib/main.dart` — load the URL store before `runApp`.
- Modify `mobile/lib/core/router/router.dart` — `/connect` gate + route + merged `refreshListenable`.
- Create `mobile/lib/features/connect/presentation/connect_screen.dart` — first-run + edit UI with probe.
- Modify `mobile/lib/features/settings/presentation/settings_screen.dart` — tappable Backend row.
- Modify `docs/superpowers/plans/2026-06-10-mobile-flutter-app.md`, `docs/superpowers/specs/2026-06-10-mobile-flutter-app-design.md` — scrub the domain strings.
- Create `mobile/test/core/network/api_base_url_test.dart` — pure-helper unit tests.
- Create `mobile/test/features/connect/connect_screen_test.dart` — validation widget test.

---

## Task 1: Base-URL store + pure helpers

**Files:**
- Create: `mobile/lib/core/network/api_base_url.dart`
- Test: `mobile/test/core/network/api_base_url_test.dart`

- [ ] **Step 1: Write the failing test**

```dart
// mobile/test/core/network/api_base_url_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/network/api_base_url.dart';

void main() {
  group('normalizeBaseUrl', () {
    test('trims whitespace and strips a single trailing slash', () {
      expect(normalizeBaseUrl('  https://api.example.com/  '), 'https://api.example.com');
      expect(normalizeBaseUrl('https://api.example.com'), 'https://api.example.com');
      expect(normalizeBaseUrl('http://1.2.3.4:8000/'), 'http://1.2.3.4:8000');
    });
    test('empty/whitespace -> empty string', () {
      expect(normalizeBaseUrl(''), '');
      expect(normalizeBaseUrl('   '), '');
    });
  });

  group('isValidBaseUrl', () {
    test('accepts http/https with a host', () {
      expect(isValidBaseUrl('https://api.example.com'), isTrue);
      expect(isValidBaseUrl('http://1.2.3.4:8000'), isTrue);
    });
    test('rejects empty, schemeless, non-http, hostless', () {
      expect(isValidBaseUrl(''), isFalse);
      expect(isValidBaseUrl('notaurl'), isFalse);
      expect(isValidBaseUrl('ftp://example.com'), isFalse);
      expect(isValidBaseUrl('https://'), isFalse);
      expect(isValidBaseUrl('api.example.com'), isFalse);
    });
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && flutter test test/core/network/api_base_url_test.dart`
Expected: FAIL — can't resolve `api_base_url.dart` / undefined functions.

- [ ] **Step 3: Write minimal implementation**

```dart
// mobile/lib/core/network/api_base_url.dart
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'session.dart';

const _kApiBaseUrl = 'api_base_url';

/// Trim, drop empties, strip a single trailing slash. Scheme left untouched.
String normalizeBaseUrl(String raw) {
  var s = raw.trim();
  if (s.isEmpty) return '';
  if (s.endsWith('/')) s = s.substring(0, s.length - 1);
  return s;
}

/// A syntactically usable backend base URL: http/https scheme + non-empty host.
bool isValidBaseUrl(String raw) {
  final s = normalizeBaseUrl(raw);
  if (s.isEmpty) return false;
  final uri = Uri.tryParse(s);
  if (uri == null) return false;
  if (uri.scheme != 'http' && uri.scheme != 'https') return false;
  return uri.host.isNotEmpty;
}

/// The active backend base URL, persisted in the platform keychain/keystore.
/// Mirrors [SessionStore]: a [ChangeNotifier] so the router can use it as a
/// refreshListenable and Dio can rebuild when it changes.
class ApiBaseUrlStore extends ChangeNotifier {
  ApiBaseUrlStore(this._storage);

  final FlutterSecureStorage _storage;
  String _baseUrl = '';

  String get baseUrl => _baseUrl;
  bool get isConfigured => _baseUrl.isNotEmpty;

  Future<void> load() async {
    _baseUrl = normalizeBaseUrl(await _storage.read(key: _kApiBaseUrl) ?? '');
    notifyListeners();
  }

  Future<void> set(String url) async {
    final next = normalizeBaseUrl(url);
    _baseUrl = next;
    if (next.isEmpty) {
      await _storage.delete(key: _kApiBaseUrl);
    } else {
      await _storage.write(key: _kApiBaseUrl, value: next);
    }
    notifyListeners();
  }
}

final apiBaseUrlProvider = ChangeNotifierProvider<ApiBaseUrlStore>(
  (ref) => ApiBaseUrlStore(ref.watch(secureStorageProvider)),
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mobile && flutter test test/core/network/api_base_url_test.dart`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/core/network/api_base_url.dart mobile/test/core/network/api_base_url_test.dart
git commit -m "feat(mobile): persisted ApiBaseUrlStore + url helpers"
```

---

## Task 2: Remove the hardcoded URL; Dio reads it at runtime

**Files:**
- Modify: `mobile/lib/core/network/api_config.dart`
- Modify: `mobile/lib/core/network/api_client.dart:86-102`

- [ ] **Step 1: Drop the hardcoded URL from ApiConfig**

Replace the whole of `api_config.dart` with:

```dart
/// Backend API timeouts. The base URL is configured at runtime by the user
/// (see ApiBaseUrlStore) — there is intentionally no hardcoded default.
abstract class ApiConfig {
  static const Duration connectTimeout = Duration(seconds: 15);
  static const Duration receiveTimeout = Duration(seconds: 30);
}
```

- [ ] **Step 2: Make `dioProvider` use the runtime base URL**

In `api_client.dart`, add the import near the other network imports:

```dart
import 'api_base_url.dart';
```

Replace `dioProvider` (lines 86-102) with:

```dart
final dioProvider = Provider<Dio>((ref) {
  // read (not watch) the session: the interceptor reads `.token` live per
  // request, so a token change never needs a Dio rebuild. The base URL, by
  // contrast, IS watched — changing instances rebuilds Dio with the new URL.
  final session = ref.read(sessionProvider);
  final baseUrl = ref.watch(apiBaseUrlProvider).baseUrl;
  final dio = Dio(BaseOptions(
    baseUrl: baseUrl,
    connectTimeout: ApiConfig.connectTimeout,
    receiveTimeout: ApiConfig.receiveTimeout,
    headers: {'Accept': 'application/json'},
  ));
  dio.interceptors.add(AuthInterceptor(session, () {
    // Clearing the session triggers the router's redirect to /login.
    session.clear();
  }));
  return dio;
});
```

- [ ] **Step 3: Verify analysis**

Run: `cd mobile && flutter analyze lib/core/network`
Expected: errors ONLY in `session.dart` (still references the removed `ApiConfig.baseUrl`) — fixed in Task 3. `api_config.dart` / `api_client.dart` clean.

- [ ] **Step 4: Commit (after Task 3 makes analyze fully clean — see Task 3 Step 4)**

(No commit here; bundled with Task 3 since the two leave the tree non-analyzing in between.)

---

## Task 3: Session widget-creds use the runtime URL; load the store at startup

**Files:**
- Modify: `mobile/lib/core/network/session.dart`
- Modify: `mobile/lib/main.dart`

- [ ] **Step 1: Inject a base-URL getter into SessionStore**

In `session.dart`: remove `import 'api_config.dart';` (line 6). Change the constructor + field and `_syncWidgetCreds`:

Replace:
```dart
class SessionStore extends ChangeNotifier {
  SessionStore(this._storage);

  final FlutterSecureStorage _storage;
```
with:
```dart
class SessionStore extends ChangeNotifier {
  SessionStore(this._storage, [this._apiBaseUrl]);

  final FlutterSecureStorage _storage;
  /// Live getter for the active API base URL (mirrored to the home widget).
  final String Function()? _apiBaseUrl;
```

Replace the `widget_api_base` line inside `_syncWidgetCreds`:
```dart
      await HomeWidget.saveWidgetData<String>('widget_api_base', ApiConfig.baseUrl);
```
with:
```dart
      await HomeWidget.saveWidgetData<String>('widget_api_base', _apiBaseUrl?.call() ?? '');
```

- [ ] **Step 2: Wire the getter in `sessionProvider`**

Replace `sessionProvider` (bottom of `session.dart`):
```dart
final sessionProvider = ChangeNotifierProvider<SessionStore>(
  (ref) => SessionStore(ref.watch(secureStorageProvider)),
);
```
with:
```dart
final sessionProvider = ChangeNotifierProvider<SessionStore>((ref) {
  // read (not watch) the URL store so a URL change does not rebuild the
  // session (which would drop the in-memory token); the closure reads it live.
  final urlStore = ref.read(apiBaseUrlProvider);
  return SessionStore(ref.watch(secureStorageProvider), () => urlStore.baseUrl);
});
```

Add the import at the top of `session.dart`:
```dart
import 'api_base_url.dart';
```
(Note: `api_base_url.dart` imports `session.dart` for `secureStorageProvider`; this is a mutual top-level reference but not a circular *initialization* — both only reference the other's top-level provider/symbol, which Dart resolves fine. If the analyzer flags a cycle, move `secureStorageProvider` into `api_base_url.dart` is NOT needed — Dart allows mutual imports.)

- [ ] **Step 3: Load the store before `runApp`**

In `main.dart`, add the import:
```dart
import 'core/network/api_base_url.dart';
```
After `final container = ProviderContainer(...)` and before `await container.read(sessionProvider).load();`, add:
```dart
  // Load the configured API base URL first so the very first router redirect
  // sees the correct configured/unconfigured state, and the session's widget
  // sync mirrors the right URL.
  await container.read(apiBaseUrlProvider).load();
```

- [ ] **Step 4: Verify analysis + run network tests**

Run: `cd mobile && flutter analyze lib/core`
Expected: "No issues found!"
Run: `cd mobile && flutter test test/core/network/api_base_url_test.dart`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/core/network/api_config.dart mobile/lib/core/network/api_client.dart \
        mobile/lib/core/network/session.dart mobile/lib/main.dart
git commit -m "feat(mobile): runtime API base URL (Dio + widget creds + startup load)"
```

---

## Task 4: Router gate to `/connect`

**Files:**
- Modify: `mobile/lib/core/router/router.dart`

- [ ] **Step 1: Add the import**

```dart
import '../network/api_base_url.dart';
import '../../features/connect/presentation/connect_screen.dart';
```

- [ ] **Step 2: Merge the store into refreshListenable + add the gate**

Replace the provider opening (lines 33-43) up to the start of `redirect`:
```dart
final goRouterProvider = Provider<GoRouter>((ref) {
  final session = ref.read(sessionProvider);

  return GoRouter(
    navigatorKey: _rootKey,
    initialLocation: '/dashboard',
    refreshListenable: session,
    redirect: (context, state) {
      final loggedIn = session.isAuthenticated;
      final loc = state.matchedLocation;
      final onLogin = loc == '/login';
      final onOnboarding = loc == '/onboarding';
```
with:
```dart
final goRouterProvider = Provider<GoRouter>((ref) {
  final session = ref.read(sessionProvider);
  final urlStore = ref.read(apiBaseUrlProvider);

  return GoRouter(
    navigatorKey: _rootKey,
    initialLocation: '/dashboard',
    refreshListenable: Listenable.merge([session, urlStore]),
    redirect: (context, state) {
      final loc = state.matchedLocation;
      final onConnect = loc == '/connect';
      // Gate everything behind a configured backend URL.
      if (!urlStore.isConfigured) {
        return onConnect ? null : '/connect';
      }
      final loggedIn = session.isAuthenticated;
      final onLogin = loc == '/login';
      final onOnboarding = loc == '/onboarding';
```

- [ ] **Step 3: Register the `/connect` route**

Immediately after the `routes: [` line, add:
```dart
      GoRoute(
        path: '/connect',
        builder: (_, _) => const ConnectScreen(),
      ),
```

- [ ] **Step 4: Verify analysis**

Run: `cd mobile && flutter analyze lib/core/router`
Expected: errors only "ConnectScreen isn't defined" (created in Task 5).

- [ ] **Step 5: Commit (bundled with Task 5)** — the router doesn't analyze until ConnectScreen exists.

---

## Task 5: Connect screen (first-run + edit, with connection probe)

**Files:**
- Create: `mobile/lib/features/connect/presentation/connect_screen.dart`
- Test: `mobile/test/features/connect/connect_screen_test.dart`

- [ ] **Step 1: Write the screen**

```dart
// mobile/lib/features/connect/presentation/connect_screen.dart
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/network/api_base_url.dart';
import '../../../core/network/session.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_text_styles.dart';
import '../../../core/widgets/app_button.dart';

/// First-run + Settings "edit instance" screen. Captures the backend base URL,
/// probes `GET {url}/health`, and saves it. When reached via the router gate
/// (unconfigured) there is no back affordance; when pushed from Settings the
/// system back works.
class ConnectScreen extends ConsumerStatefulWidget {
  const ConnectScreen({super.key});

  @override
  ConsumerState<ConnectScreen> createState() => _ConnectScreenState();
}

class _ConnectScreenState extends ConsumerState<ConnectScreen> {
  late final TextEditingController _controller;
  String? _error;
  bool _probing = false;
  bool _probeFailed = false; // when true, the button becomes "Save anyway"

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: ref.read(apiBaseUrlProvider).baseUrl);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _save(String url, {required bool requireProbe}) async {
    final store = ref.read(apiBaseUrlProvider);
    final wasConfigured = store.isConfigured;
    final previous = store.baseUrl;
    await store.set(url);
    // Changing to a different instance invalidates the existing session.
    if (wasConfigured && normalizeBaseUrl(url) != previous) {
      await ref.read(sessionProvider).clear();
    }
    // The merged refreshListenable re-runs the router redirect, which routes to
    // /login (or /dashboard if still authed on the same instance).
    if (mounted && Navigator.of(context).canPop()) {
      context.go('/login');
    }
  }

  Future<void> _testAndConnect() async {
    final raw = _controller.text;
    if (!isValidBaseUrl(raw)) {
      setState(() {
        _error = 'Enter a valid URL, e.g. https://your-instance.example.com';
        _probeFailed = false;
      });
      return;
    }
    final url = normalizeBaseUrl(raw);
    setState(() {
      _error = null;
      _probing = true;
      _probeFailed = false;
    });
    final reachable = await _probe(url);
    if (!mounted) return;
    if (reachable) {
      await _save(url, requireProbe: true);
      return;
    }
    setState(() {
      _probing = false;
      _probeFailed = true;
      _error = "Couldn't reach $url/health. Check the URL, or save anyway.";
    });
  }

  Future<bool> _probe(String url) async {
    try {
      final dio = Dio(BaseOptions(
        baseUrl: url,
        connectTimeout: const Duration(seconds: 4),
        receiveTimeout: const Duration(seconds: 4),
        headers: {'Accept': 'application/json'},
      ));
      final res = await dio.get<dynamic>('/health');
      return res.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  @override
  Widget build(BuildContext context) {
    final canPop = Navigator.of(context).canPop();
    return Scaffold(
      backgroundColor: AppColors.canvas,
      appBar: canPop ? AppBar(backgroundColor: Colors.transparent, elevation: 0) : null,
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Connect to your instance', style: AppTextStyles.h2),
                const SizedBox(height: 8),
                Text(
                  'Enter the URL of your IntelliStock backend. You can change this '
                  'later in Settings.',
                  style: AppTextStyles.body.copyWith(color: AppColors.textMuted),
                ),
                const SizedBox(height: 20),
                TextField(
                  controller: _controller,
                  autocorrect: false,
                  enableSuggestions: false,
                  keyboardType: TextInputType.url,
                  style: AppTextStyles.body.copyWith(color: AppColors.textHi),
                  decoration: InputDecoration(
                    hintText: 'https://your-instance.example.com',
                    hintStyle: AppTextStyles.body.copyWith(color: AppColors.textDim),
                    errorText: _error,
                    filled: true,
                    fillColor: AppColors.surface,
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(12),
                      borderSide: BorderSide(color: AppColors.border),
                    ),
                  ),
                  onChanged: (_) {
                    if (_error != null || _probeFailed) {
                      setState(() {
                        _error = null;
                        _probeFailed = false;
                      });
                    }
                  },
                  onSubmitted: (_) => _testAndConnect(),
                ),
                const SizedBox(height: 20),
                SizedBox(
                  width: double.infinity,
                  child: AppButton(
                    label: _probing
                        ? 'Testing…'
                        : (_probeFailed ? 'Save anyway' : 'Test & Connect'),
                    onPressed: _probing
                        ? null
                        : (_probeFailed
                            ? () => _save(_controller.text, requireProbe: false)
                            : _testAndConnect),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
```

NOTE: confirm the real `AppButton` API (`label`/`onPressed` names) and `AppTextStyles.h2`/`body` exist in `core/widgets/app_button.dart` / `app_text_styles.dart`; adjust the call to match (Task verified in Step 3 via analyze).

- [ ] **Step 2: Write a validation widget test**

```dart
// mobile/test/features/connect/connect_screen_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/network/api_base_url.dart';
import 'package:intellistock_mobile/features/connect/presentation/connect_screen.dart';

void main() {
  testWidgets('invalid URL shows an inline error and does not save', (tester) async {
    await tester.pumpWidget(const ProviderScope(
      child: MaterialApp(home: ConnectScreen()),
    ));
    await tester.enterText(find.byType(TextField), 'notaurl');
    await tester.tap(find.text('Test & Connect'));
    await tester.pump();
    expect(find.textContaining('valid URL'), findsOneWidget);
  });
}
```

- [ ] **Step 3: Verify analysis + run the test**

Run: `cd mobile && flutter analyze lib/features/connect lib/core/router`
Expected: "No issues found!" (fix any `AppButton`/`AppTextStyles` name mismatches surfaced here).
Run: `cd mobile && flutter test test/features/connect/connect_screen_test.dart`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add mobile/lib/features/connect/presentation/connect_screen.dart \
        mobile/test/features/connect/connect_screen_test.dart \
        mobile/lib/core/router/router.dart
git commit -m "feat(mobile): /connect screen + first-run gate (URL + health probe)"
```

---

## Task 6: Editable Backend row in Settings

**Files:**
- Modify: `mobile/lib/features/settings/presentation/settings_screen.dart:340-346`

- [ ] **Step 1: Add imports (if missing)**

At the top of `settings_screen.dart`, ensure these are present (add any that are not):
```dart
import 'package:go_router/go_router.dart';
import '../../../core/network/api_base_url.dart';
```

- [ ] **Step 2: Make the Backend row tappable + show the runtime URL**

Replace the Backend `_SettingsRow` (lines 340-346):
```dart
                          _SettingsRow(
                            icon: symbol('database'),
                            iconColor: AppColors.info,
                            title: 'Backend',
                            subtitle: ApiConfig.baseUrl,
                            trailing: null,
                          ),
```
with:
```dart
                          _SettingsRow(
                            icon: symbol('database'),
                            iconColor: AppColors.info,
                            title: 'Backend',
                            subtitle: ref.watch(apiBaseUrlProvider).baseUrl,
                            trailing: Icon(symbol('arrow_forward'),
                                size: 16, color: AppColors.textDim),
                            onTap: () => context.push('/connect'),
                          ),
```

If `settings_screen.dart` still imports `api_config.dart` only for this line and no longer uses it, remove that import.

- [ ] **Step 3: Verify analysis**

Run: `cd mobile && flutter analyze lib/features/settings`
Expected: "No issues found!"

- [ ] **Step 4: Commit**

```bash
git add mobile/lib/features/settings/presentation/settings_screen.dart
git commit -m "feat(mobile): editable backend URL row in Settings"
```

---

## Task 7: Scrub the domain from docs + verify zero references

**Files:**
- Modify: `docs/superpowers/plans/2026-06-10-mobile-flutter-app.md`
- Modify: `docs/superpowers/specs/2026-06-10-mobile-flutter-app-design.md`

- [ ] **Step 1: Replace the literal domain strings with a placeholder**

In both files, replace every occurrence of the former hardcoded backend host with `https://your-instance.example.com` (or, in prose describing the old default, reword to "the configured backend URL"). Keep surrounding sentences sensible. Scrub this spec/plan pair too, so the former host string appears nowhere in the repo.

- [ ] **Step 2: Verify the repo is clean of the former host**

Run a recursive, case-sensitive search for the former backend host (excluding `.git`/`build`/`.dart_tool`) and confirm zero matches. Build the search term at the shell so this document does not itself contain the literal:
```bash
cd /Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock
host="pkrishna""\.dev"; grep -rnIE "$host" . --exclude-dir=.git --exclude-dir=build --exclude-dir=.dart_tool || echo "CLEAN: no references"
```
Expected: `CLEAN: no references`.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/2026-06-10-mobile-flutter-app.md \
        docs/superpowers/specs/2026-06-10-mobile-flutter-app-design.md
git commit -m "docs: scrub hardcoded backend domain from historical design docs"
```

---

## Task 8: Full verification + parallel bug sweep

**Files:** none (verification)

- [ ] **Step 1: Analyze + test the whole touched surface**

Run: `cd mobile && flutter analyze lib/core lib/features/connect lib/features/settings`
Expected: "No issues found!"
Run: `cd mobile && flutter test test/core/network/api_base_url_test.dart test/features/connect/`
Expected: all PASS.

- [ ] **Step 2: Parallel adversarial bug sweep** on `git diff main...HEAD` — correctness (store/Dio rebuild, router gate ordering, session-clear-on-change), Dart null/lifecycle safety (ConnectScreen `mounted`/`canPop`, mutual import), and a grep proving zero domain references. Fix real findings, re-verify.

- [ ] **Step 3: Build sanity (optional, slow)**

Run: `cd mobile && flutter build ios --debug --no-codesign` (or rely on `scripts/deploy.sh 1` at deploy time).
Expected: build succeeds (the removed `--dart-define` doesn't break the build).

---

## Self-Review

**Spec coverage:** zero-hardcoded-URL → Task 2 (code) + Task 7 (docs, grep-verified); first-run prompt → Tasks 4+5; editable in Settings → Task 6; runtime application (Dio/widget/startup) → Tasks 2+3; drop dart-define → Task 2; format+probe validation → Task 5; clear-session-on-change → Task 5 `_save`. ✅

**Placeholder scan:** no TBD/TODO; the two runtime-confirm notes (AppButton API in Task 5; settings imports in Task 6) are explicit analyze-gated verification steps with concrete fixes, not gaps. ✅

**Type consistency:** `normalizeBaseUrl`/`isValidBaseUrl`/`ApiBaseUrlStore`/`apiBaseUrlProvider` defined in Task 1 and used identically in Tasks 2/3/4/5/6. `SessionStore` new optional `_apiBaseUrl` getter (Task 3) is backward-compatible. Router gate uses `urlStore.isConfigured` (Task 1 API). ✅

## Open items (resolve during implementation)
- Confirm `AppButton`'s constructor (`label`/`onPressed`) and `AppTextStyles.h2`/`body`/`AppColors.canvas`/`surface`/`border`/`textDim` member names against the real files; adjust ConnectScreen to match (Task 5 Step 3 analyze catches these).
- Confirm `settings_screen.dart`'s build has `ref` + `context` in scope for the Backend row (it uses `versionAsync` + `showLicensePage`, so both are available).
