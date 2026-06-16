# Session longevity + live widgets — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make login last ~30 days (sliding), un-freeze the iOS widgets, and make every "updated X ago" label tell the truth.

**Architecture:** Backend JWTs go from 24h → 30 days and carry an `iat`; `get_current_user` hands back a freshly-minted token via an `X-Refreshed-Token` response header once a token passes half-life, so active sessions slide forever. The mobile Dio interceptor and a global web `fetch` wrapper adopt that header transparently. The widget (which authenticates with the same JWT) thus stops 401-freezing; its relative-time label and a new in-app label switch to auto-updating/self-ticking renderers.

**Tech Stack:** Python/FastAPI + PyJWT (backend), Flutter/Riverpod/Dio (mobile), SwiftUI WidgetKit (iOS), Vue 3 + raw `fetch` (web).

**Branch/deploy:** Mobile + widget changes live on `feat/mobile-dashboard-ui` (deploy `mobile/scripts/deploy.sh 1`). Backend + web changes are committed in isolated commits so they can be cherry-picked onto `main` for the user's Dokploy deploy. Keep backend, web, and mobile changes in **separate commits**.

**Pre-flight (per CLAUDE.md):** Before editing `create_access_token` / `get_current_user`, run `gitnexus_impact({target, direction:"upstream"})` and report blast radius. The GitNexus index is stale — either `npx gitnexus analyze` first or verify callers manually (`grep -rn create_access_token backend`).

---

### Task 1: Backend — 30-day token, `iat` claim, renewal helpers

**Files:**
- Modify: `backend/auth_utils.py` (`create_access_token` ~231-240; add two helpers after `decode_access_token` ~254)
- Test: `tests/test_auth_token.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_token.py
"""Tests for JWT lifetime + sliding-renewal helpers in backend/auth_utils.py."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

pytest.importorskip("jwt")  # PyJWT

import auth_utils  # noqa: E402


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-key")
    monkeypatch.delenv("JWT_EXPIRE_HOURS", raising=False)


def _decode(token: str) -> dict:
    import jwt
    return jwt.decode(token, "test-secret-key", algorithms=["HS256"])


def test_default_lifetime_is_30_days_and_has_iat():
    token = auth_utils.create_access_token("u1", "alice", "user")
    payload = _decode(token)
    assert "iat" in payload and "exp" in payload
    lifetime_hours = (payload["exp"] - payload["iat"]) / 3600
    assert 719 <= lifetime_hours <= 721  # ~720h / 30 days


def test_lifetime_honors_env_override(monkeypatch):
    monkeypatch.setenv("JWT_EXPIRE_HOURS", "48")
    payload = _decode(auth_utils.create_access_token("u1", "alice", "user"))
    assert 47 <= (payload["exp"] - payload["iat"]) / 3600 <= 49


def test_needs_refresh_true_past_halflife():
    now = datetime(2026, 6, 16, 12, 0, 0)
    iat = now - timedelta(days=20)          # 20 of 30 days elapsed -> past half-life
    exp = iat + timedelta(days=30)
    payload = {"sub": "u1", "username": "alice", "role": "user",
               "iat": int(iat.timestamp()), "exp": int(exp.timestamp())}
    assert auth_utils.token_needs_refresh(payload, now=now) is True


def test_needs_refresh_false_when_fresh():
    now = datetime(2026, 6, 16, 12, 0, 0)
    iat = now - timedelta(days=2)           # only 2 of 30 days elapsed
    exp = iat + timedelta(days=30)
    payload = {"iat": int(iat.timestamp()), "exp": int(exp.timestamp())}
    assert auth_utils.token_needs_refresh(payload, now=now) is False


def test_needs_refresh_false_without_iat():
    # Legacy 24h tokens have no iat -> never slide.
    assert auth_utils.token_needs_refresh({"exp": 9999999999}) is False


def test_renewed_token_if_stale_mints_when_past_halflife():
    now = datetime(2026, 6, 16, 12, 0, 0)
    iat = now - timedelta(days=20)
    exp = iat + timedelta(days=30)
    payload = {"sub": "u1", "username": "alice", "role": "admin",
               "iat": int(iat.timestamp()), "exp": int(exp.timestamp())}
    fresh = auth_utils.renewed_token_if_stale(payload, now=now)
    assert fresh is not None
    new_payload = _decode(fresh)
    assert new_payload["sub"] == "u1" and new_payload["role"] == "admin"


def test_renewed_token_if_stale_returns_none_when_fresh():
    now = datetime(2026, 6, 16, 12, 0, 0)
    iat = now - timedelta(days=1)
    exp = iat + timedelta(days=30)
    payload = {"sub": "u1", "username": "alice",
               "iat": int(iat.timestamp()), "exp": int(exp.timestamp())}
    assert auth_utils.renewed_token_if_stale(payload, now=now) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock && python -m pytest tests/test_auth_token.py -v`
Expected: FAIL — `token_needs_refresh` / `renewed_token_if_stale` not defined, and lifetime test fails (currently 24h).

- [ ] **Step 3: Edit `create_access_token` (default 720h + `iat`)**

Add `import calendar` to the top imports of `backend/auth_utils.py` (next to `import hmac`/`import os`). Then replace the body of `create_access_token`:

```python
def create_access_token(user_id: str, username: str, role: str) -> str:
    if not _check_jwt():
        raise RuntimeError("PyJWT is required for auth. Install with: pip install pyjwt")
    import jwt
    secret = os.environ.get("JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError("JWT_SECRET environment variable is required for auth")
    now = datetime.utcnow()
    hours = int(os.environ.get("JWT_EXPIRE_HOURS", "720"))  # default ~30 days
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=hours),
    }
    return jwt.encode(payload, secret, algorithm="HS256")
```

- [ ] **Step 4: Add the renewal helpers after `decode_access_token`**

```python
def token_needs_refresh(payload: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """True when a token is past the halfway point of its lifetime.

    Requires both ``iat`` and ``exp`` (unix seconds, as PyJWT returns them).
    Tokens minted before sliding renewal shipped have no ``iat`` and never
    refresh — they expire once, then the user gets a fresh sliding token.
    """
    iat = payload.get("iat")
    exp = payload.get("exp")
    if not isinstance(iat, (int, float)) or not isinstance(exp, (int, float)):
        return False
    lifetime = exp - iat
    if lifetime <= 0:
        return False
    now_dt = now or datetime.utcnow()
    # Treat the naive datetime as UTC (matches utcnow + PyJWT's UTC encoding).
    now_ts = calendar.timegm(now_dt.utctimetuple())
    return (exp - now_ts) < lifetime / 2


def renewed_token_if_stale(payload: Dict[str, Any], now: Optional[datetime] = None) -> Optional[str]:
    """Return a freshly-minted token when ``payload`` is past half-life, else None."""
    if not token_needs_refresh(payload, now):
        return None
    sub = payload.get("sub")
    username = payload.get("username")
    role = payload.get("role", "user")
    if not sub or not username:
        return None
    return create_access_token(str(sub), str(username), str(role))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_auth_token.py -v`
Expected: PASS (all 7).

- [ ] **Step 6: Commit (backend — cherry-pickable to main)**

```bash
git add backend/auth_utils.py tests/test_auth_token.py
git commit -m "feat(auth): 30-day JWT + iat claim + sliding-renewal helpers"
```

---

### Task 2: Backend — sliding renewal header in `get_current_user` + CORS

**Files:**
- Modify: `backend/api/main.py` (import `Response` ~25; import helper ~50-51; `get_current_user` ~365-384; CORS block ~187-193)

- [ ] **Step 1: Add `Response` to the FastAPI import (line 25)**

```python
from fastapi import FastAPI, HTTPException, Depends, Request, Response
```

- [ ] **Step 2: Import the renewal helper (auth_utils import block, ~50-51)**

Add `renewed_token_if_stale` to the existing `from auth_utils import (...)` block that already imports `create_access_token, decode_access_token`.

- [ ] **Step 3: Wire renewal into `get_current_user`**

Replace the function (`backend/api/main.py:365`) with:

```python
def get_current_user(
    response: Response,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    conn=Depends(conn_dependency),
) -> dict:
    """Validate JWT and return current user dict (id, username, role). Raises 401 if invalid."""
    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = get_user_by_id(conn, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    # Sliding renewal: once the token passes half-life, hand back a fresh one via
    # a response header so active sessions never reach expiry. Best-effort — a
    # renewal hiccup must never break the request.
    try:
        renewed = renewed_token_if_stale(payload)
        if renewed:
            response.headers["X-Refreshed-Token"] = renewed
    except Exception:
        pass
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "role": user.get("role", "user"),
    }
```

- [ ] **Step 4: Expose the header through CORS (CORS block, ~192)**

```python
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Refreshed-Token"],
```

- [ ] **Step 5: Verify the app imports cleanly**

Run: `cd /Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock && python -c "import sys; sys.path.insert(0,'backend'); import auth_utils; print('auth_utils ok')"`
Expected: prints `auth_utils ok` (no import error). Full `import api.main` may need a DB/env; if it errors on config, confirm the error is unrelated to our edits (syntax/NameError would be ours).

- [ ] **Step 6: Run the auth test suite again (no regressions)**

Run: `python -m pytest tests/test_auth_token.py -v`
Expected: PASS.

- [ ] **Step 7: Commit (backend — cherry-pickable to main)**

```bash
git add backend/api/main.py
git commit -m "feat(auth): slide token via X-Refreshed-Token header on authed requests"
```

---

### Task 3: Mobile — adopt `X-Refreshed-Token` (SessionStore + interceptor)

**Files:**
- Modify: `mobile/lib/core/network/session.dart` (add `setToken` after `setSession` ~68)
- Modify: `mobile/lib/core/network/api_client.dart` (add helper + `AuthInterceptor.onResponse`)
- Test: `mobile/test/core/network/auth_interceptor_test.dart` (new)

- [ ] **Step 1: Write the failing test**

```dart
// mobile/test/core/network/auth_interceptor_test.dart
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/network/api_client.dart';

void main() {
  group('refreshedTokenFromHeaders', () {
    test('returns the token when X-Refreshed-Token is present', () {
      final h = Headers.fromMap({'X-Refreshed-Token': ['new.jwt.token']});
      expect(refreshedTokenFromHeaders(h), 'new.jwt.token');
    });

    test('returns null when the header is absent', () {
      final h = Headers.fromMap({'Content-Type': ['application/json']});
      expect(refreshedTokenFromHeaders(h), isNull);
    });

    test('returns null when the header is blank', () {
      final h = Headers.fromMap({'X-Refreshed-Token': ['']});
      expect(refreshedTokenFromHeaders(h), isNull);
    });
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock/mobile && flutter test test/core/network/auth_interceptor_test.dart`
Expected: FAIL — `refreshedTokenFromHeaders` undefined.

- [ ] **Step 3: Add the helper + `onResponse` to `api_client.dart`**

At the top of `mobile/lib/core/network/api_client.dart` (after imports), add:

```dart
/// Response header the backend uses to hand back a slid (renewed) token.
const _kRefreshedTokenHeader = 'x-refreshed-token';

/// The renewed JWT carried by a response, or null when absent/blank.
String? refreshedTokenFromHeaders(Headers headers) {
  final v = headers.value(_kRefreshedTokenHeader);
  return (v == null || v.isEmpty) ? null : v;
}
```

Add this method to `AuthInterceptor` (after `onRequest`):

```dart
  @override
  void onResponse(Response response, ResponseInterceptorHandler handler) {
    final fresh = refreshedTokenFromHeaders(response.headers);
    if (fresh != null) {
      // Fire-and-forget: persist the slid token + re-mirror it to the widget.
      _session.setToken(fresh);
    }
    handler.next(response);
  }
```

- [ ] **Step 4: Add `setToken` to `SessionStore` (`session.dart`, after `setSession`)**

```dart
  /// Replace just the JWT (e.g. a sliding-renewal token handed back by the
  /// backend), keeping the cached user. Persists it and re-mirrors it to the
  /// widget so the widget's self-refresh keeps working. No-op if unchanged.
  Future<void> setToken(String token) async {
    if (token.isEmpty || token == _token) return;
    _token = token;
    await _storage.write(key: _kToken, value: token);
    await _syncWidgetCreds();
    notifyListeners();
  }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd mobile && flutter test test/core/network/auth_interceptor_test.dart`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit (mobile — stays on feat branch)**

```bash
git add mobile/lib/core/network/session.dart mobile/lib/core/network/api_client.dart mobile/test/core/network/auth_interceptor_test.dart
git commit -m "feat(mobile): adopt sliding X-Refreshed-Token so sessions never expire mid-use"
```

---

### Task 4: Mobile — self-ticking `RelativeTimeText` widget

**Files:**
- Modify: `mobile/lib/core/formatters/formatters.dart` (`fmtRelative` gains optional `now`)
- Create: `mobile/lib/core/widgets/relative_time_text.dart`
- Test: `mobile/test/core/widgets/relative_time_text_test.dart` (new)

- [ ] **Step 1: Write the failing test**

```dart
// mobile/test/core/widgets/relative_time_text_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/widgets/relative_time_text.dart';

void main() {
  testWidgets('renders relative time and ticks as the clock advances',
      (tester) async {
    var fakeNow = DateTime(2026, 6, 16, 12, 0, 0);
    final ts = DateTime(2026, 6, 16, 11, 58, 0); // 2 minutes earlier

    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: RelativeTimeText(
          timestamp: ts,
          tick: const Duration(seconds: 1),
          clock: () => fakeNow,
        ),
      ),
    ));

    expect(find.text('2m ago'), findsOneWidget);

    // Advance the injected clock by 3 minutes, let one tick fire.
    fakeNow = DateTime(2026, 6, 16, 12, 3, 0); // now 5 minutes after ts
    await tester.pump(const Duration(seconds: 1));

    expect(find.text('5m ago'), findsOneWidget);
  });

  testWidgets('shows "Just now" for a fresh timestamp', (tester) async {
    final now = DateTime(2026, 6, 16, 12, 0, 0);
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: RelativeTimeText(
          timestamp: now.subtract(const Duration(seconds: 5)),
          clock: () => now,
        ),
      ),
    ));
    expect(find.text('Just now'), findsOneWidget);
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mobile && flutter test test/core/widgets/relative_time_text_test.dart`
Expected: FAIL — `relative_time_text.dart` does not exist.

- [ ] **Step 3: Add optional `now` to `fmtRelative`**

In `mobile/lib/core/formatters/formatters.dart`, change `fmtRelative`:

```dart
/// Relative time: `Just now` / `5m ago` / `2h ago` / `3d ago`.
/// Pass [now] to compute against a fixed clock (testing / self-ticking widgets).
String fmtRelative(dynamic v, {DateTime? now}) {
  final dt = parseDateTime(v);
  if (dt == null) return _dash;
  final diff = (now ?? DateTime.now()).difference(dt);
  if (diff.inSeconds < 60) return 'Just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
  if (diff.inHours < 24) return '${diff.inHours}h ago';
  return '${diff.inDays}d ago';
}
```

- [ ] **Step 4: Create `RelativeTimeText`**

```dart
// mobile/lib/core/widgets/relative_time_text.dart
import 'dart:async';
import 'package:flutter/material.dart';
import '../formatters/formatters.dart';

/// A `Text` that shows how long ago [timestamp] was ("Just now", "5m ago", …)
/// and re-renders itself on a timer so the label stays honest without any
/// parent rebuild. Pass [clock] in tests to control "now".
class RelativeTimeText extends StatefulWidget {
  const RelativeTimeText({
    super.key,
    required this.timestamp,
    this.style,
    this.tick = const Duration(seconds: 20),
    this.clock,
  });

  final DateTime? timestamp;
  final TextStyle? style;
  final Duration tick;
  final DateTime Function()? clock;

  @override
  State<RelativeTimeText> createState() => _RelativeTimeTextState();
}

class _RelativeTimeTextState extends State<RelativeTimeText> {
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(widget.tick, (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final ts = widget.timestamp;
    if (ts == null) return const SizedBox.shrink();
    final now = (widget.clock ?? DateTime.now)();
    return Text(fmtRelative(ts, now: now), style: widget.style);
  }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mobile && flutter test test/core/widgets/relative_time_text_test.dart`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit (mobile)**

```bash
git add mobile/lib/core/formatters/formatters.dart mobile/lib/core/widgets/relative_time_text.dart mobile/test/core/widgets/relative_time_text_test.dart
git commit -m "feat(mobile): self-ticking RelativeTimeText + testable fmtRelative(now:)"
```

---

### Task 5: Mobile — record last successful refresh + show it on the dashboard

**Files:**
- Modify: `mobile/lib/features/dashboard/application/dashboard_controller.dart` (add provider)
- Modify: `mobile/lib/features/dashboard/presentation/portfolio_chart.dart` (stamp on success in `_HistoryNotifier`)
- Modify: `mobile/lib/features/dashboard/presentation/dashboard_screen.dart` (render label in `_PortfolioSection`)

- [ ] **Step 1: Add the provider**

Append to `mobile/lib/features/dashboard/application/dashboard_controller.dart` (ensure `package:flutter_riverpod/flutter_riverpod.dart` is imported there):

```dart
/// Wall-clock time of the last *successful* portfolio-history fetch on the
/// dashboard. Stamped by the history notifier; read by the freshness label.
final portfolioUpdatedAtProvider = StateProvider<DateTime?>((ref) => null);
```

- [ ] **Step 2: Stamp it on each successful fetch in `_HistoryNotifier`**

In `mobile/lib/features/dashboard/presentation/portfolio_chart.dart`, import the controller if not already:

```dart
import '../application/dashboard_controller.dart';
```

Add a guarded stamp helper to `_HistoryNotifier` and call it. In `build`, after `final data = await _fetch(arg);`, schedule a post-build stamp; in `_refresh`, stamp on success:

```dart
  void _stampUpdated() {
    // Guarded: the autoDispose notifier may already be gone on a late tick.
    try {
      ref.read(portfolioUpdatedAtProvider.notifier).state = DateTime.now();
    } catch (_) {/* provider disposed — ignore */}
  }
```

In `build`, change:

```dart
    final data = await _fetch(arg);
    // Stamp outside the build phase (Riverpod forbids mutating a provider
    // during another provider's build).
    Future.microtask(_stampUpdated);
```

In `_refresh`, change the success branch:

```dart
  Future<void> _refresh(_HistoryArgs arg) async {
    try {
      state = AsyncData(await _fetch(arg));
      _stampUpdated();
    } catch (_) {
      // keep the last good data on a transient poll failure
    }
  }
```

- [ ] **Step 3: Render the label in `_PortfolioSection`**

In `mobile/lib/features/dashboard/presentation/dashboard_screen.dart`, add imports:

```dart
import '../../../core/widgets/relative_time_text.dart';
```

(`dashboard_controller.dart` is already imported at line 16.) Inside `_PortfolioSectionState.build`, below the hero value (near the range control around line 247), insert a freshness line:

```dart
            Consumer(builder: (context, ref, _) {
              final updatedAt = ref.watch(portfolioUpdatedAtProvider);
              if (updatedAt == null) return const SizedBox.shrink();
              return Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text('Updated ',
                        style: AppTextStyles.nano
                            .copyWith(color: AppColors.textFaint)),
                    RelativeTimeText(
                      timestamp: updatedAt,
                      style: AppTextStyles.nano
                          .copyWith(color: AppColors.textFaint),
                    ),
                  ],
                ),
              );
            }),
```

> Placement note: read the surrounding `_PortfolioSectionState.build` first and drop this directly beneath the hero value / change row so it reads "Updated 5m ago". `AppTextStyles.nano` + `AppColors.textFaint` are already used in this file's neighbourhood — match whatever the adjacent labels use.

- [ ] **Step 4: Analyze + run the full mobile suite**

Run: `cd mobile && flutter analyze && flutter test`
Expected: analyze clean except the 1 known pre-existing `live_state_notifier.dart` doc-comment info; all tests PASS (previous 336 + the new ones).

- [ ] **Step 5: Commit (mobile)**

```bash
git add mobile/lib/features/dashboard/
git commit -m "feat(mobile): live 'Updated Xm ago' on dashboard, stamped on each successful poll"
```

---

### Task 6: iOS widget — auto-updating relative label (home + lock screen)

**Files:**
- Modify: `mobile/ios/PortfolioWidget/PortfolioWidget.swift` (home overlay ~208-214; lock rectangular ~230-233; timeline comment ~482-484)

- [ ] **Step 1: Replace the home-screen overlay label (~208-214)**

```swift
        .overlay(alignment: .bottomTrailing) {
            if entry.hasData, entry.syncedAt > 0 {
                // Auto-updating relative text: iOS re-renders this on its own
                // clock between timeline reloads, so it never sticks on "Just now".
                (Text(Date(timeIntervalSince1970: entry.syncedAt), style: .relative)
                    + Text(" ago"))
                    .font(.system(size: 9)).foregroundColor(cFaint)
                    .padding(.trailing, 10).padding(.bottom, 7)
            }
        }
```

- [ ] **Step 2: Replace the lock-screen rectangular label (~230-233)**

```swift
                if entry.syncedAt > 0 {
                    (Text(Date(timeIntervalSince1970: entry.syncedAt), style: .relative)
                        + Text(" ago"))
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
```

- [ ] **Step 3: Clarify the timeline interval comment (~482-484)**

Keep the existing `let interval = max(configuration.refresh.seconds, 600)` logic; update the comment to be accurate:

```swift
        // iOS budgets background widget reloads (effectively ~every 15-30 min)
        // and throttles further on usage; requesting sub-10-min just burns the
        // budget and refreshes LESS. We pass the user's interval through with a
        // 10-min floor. The relative-time label is auto-updating, so it always
        // shows honest data age even between these reloads.
```

- [ ] **Step 4: Build the iOS target to confirm it compiles**

The `relativeAgo(_:)` helper may now be unused — if Swift warns "never used", delete the function (lines ~147-156). Build the app: `cd mobile && flutter build ios --debug --no-codesign` (or rely on the deploy step). Confirm no Swift compile errors.
Expected: build succeeds; widget label change ready for on-device verification.

- [ ] **Step 5: Commit (mobile)**

```bash
git add mobile/ios/PortfolioWidget/PortfolioWidget.swift
git commit -m "fix(widget): auto-updating relative label so it stops sticking on 'Just now'"
```

---

### Task 7: Web — global `fetch` wrapper to adopt `X-Refreshed-Token`

**Files:**
- Modify: `frontend/src/utils/auth.js` (add `installRefreshedTokenCapture`)
- Modify: `frontend/src/main.js` (call it once at startup)

- [ ] **Step 1: Add the capture installer to `auth.js`**

After the storage helpers (near `clearSession`) add:

```js
/**
 * Install a one-time global fetch wrapper that adopts a slid token from the
 * backend's `X-Refreshed-Token` response header. The Vue app has no central
 * HTTP client (each view calls fetch directly), so this is the single
 * chokepoint that keeps the web session sliding for ~30 days of activity.
 * Idempotent.
 */
export function installRefreshedTokenCapture() {
  if (typeof window === 'undefined' || window.__refreshTokenCaptureInstalled) return
  window.__refreshTokenCaptureInstalled = true
  const orig = window.fetch.bind(window)
  window.fetch = async (...args) => {
    const res = await orig(...args)
    try {
      const t = res.headers && res.headers.get && res.headers.get('X-Refreshed-Token')
      if (t) localStorage.setItem(TOKEN_KEY, t)
    } catch { /* never let token capture break a request */ }
    return res
  }
}
```

- [ ] **Step 2: Call it once at startup in `main.js`**

Read `frontend/src/main.js`, then add near the top (before `createApp(...).mount(...)`):

```js
import { installRefreshedTokenCapture } from './utils/auth.js'
installRefreshedTokenCapture()
```

(If `main.js` already imports from `./utils/auth.js`, merge the named import instead of adding a second import line.)

- [ ] **Step 3: Verify the web build compiles**

Run: `cd /Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock/frontend && npm run build`
Expected: build succeeds with no errors referencing `auth.js` / `main.js`.

- [ ] **Step 4: Commit (web — cherry-pickable to main)**

```bash
git add frontend/src/utils/auth.js frontend/src/main.js
git commit -m "feat(web): adopt sliding X-Refreshed-Token via a global fetch wrapper"
```

---

### Task 8: Verify, bug-sweep, and integrate

- [ ] **Step 1: Full backend auth tests**

Run: `python -m pytest tests/test_auth_token.py -v`
Expected: PASS.

- [ ] **Step 2: Full mobile suite + analyze**

Run: `cd mobile && flutter analyze && flutter test`
Expected: analyze clean (except the known pre-existing info); all tests PASS.

- [ ] **Step 3: Parallel adversarial bug sweep**

Dispatch independent reviewers (see the conversation's bug-sweep step) covering: the FastAPI `Response`-dependency header injection (streaming/error paths), the `calendar.timegm` UTC math, Dio `onResponse` re-entrancy / infinite-loop risk, the Riverpod `Future.microtask` stamp on an autoDispose notifier, the SwiftUI `.relative` style sub-minute wording, and the `window.fetch` monkey-patch interacting with non-Response returns. Fix confirmed findings; re-run Steps 1-2.

- [ ] **Step 4: Integrate to `main` for deploy**

Cherry-pick the backend + web commits (Tasks 1, 2, 7) onto `main`, push `main` (owner push) and `feat/mobile-dashboard-ui`. Tell the user to Dokploy-deploy `main` so the 30-day/sliding token + CORS take effect, then `mobile/scripts/deploy.sh 1` for the app.

---

## Self-review

**Spec coverage:**
- Part A backend (720h + iat) → Task 1. Sliding header + CORS → Task 2. ✓
- Part A mobile (setToken + onResponse) → Task 3. ✓
- Part A web (capture X-Refreshed-Token) → Task 7. ✓
- Part B widget refresh (keep interval logic, lean on token fix) → Task 6 Step 3 (comment) + Task 1-2 (token). ✓
- Part C widget label (auto-updating) → Task 6 Steps 1-2. ✓
- Part C in-app label (RelativeTimeText + provider + stamp + render) → Tasks 4 & 5. ✓
- Migration (no iat → no slide) → covered by `token_needs_refresh` returning False (Task 1 test `test_needs_refresh_false_without_iat`). ✓
- Testing/impact pre-flight → Pre-flight note + Task 8. ✓

**Placeholder scan:** No TBD/TODO. The one judgement call (exact label placement in `_PortfolioSection`) is explicitly flagged to read surrounding code and match adjacent styles — code is provided.

**Type consistency:** `refreshedTokenFromHeaders(Headers)`, `setToken(String)`, `token_needs_refresh(payload, now=)`, `renewed_token_if_stale(payload, now=)`, `portfolioUpdatedAtProvider`, `RelativeTimeText({timestamp, style, tick, clock})`, `fmtRelative(v, {now})` — names match across all tasks and tests.
