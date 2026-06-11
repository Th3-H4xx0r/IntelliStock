# Notification Routing + iOS Push + Fill-Alert Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route each of the 9 live-trading notification categories to Discord and/or iOS push per user preference, deliver iOS push via direct APNs (no Firebase), add settings UIs on the Flutter app and Vue web frontend, and harden the fill-alert pipeline so fills are reliably reported.

**Architecture:** A new `notifications.notify(category, …)` fan-out sits between `live_alerts.alert_*` and the sinks (Discord enqueue + APNs). Per-category prefs live in RethinkDB (`NotificationPreferences`), device tokens in `PushDevices`. Defaults preserve today's Discord-only behavior. Hardening adds an idempotent fill-alert ledger + reconciliation catch-up + outbox retry.

**Tech Stack:** Python/FastAPI + RethinkDB (backend), `httpx[http2]` + PyJWT/cryptography (APNs), Flutter/Riverpod + native Swift (mobile), Vue 3 `<script setup>` (web).

**Conventions:**
- Backend tests: `cd backend && python -m pytest tests/<file> -v`. Mobile: `cd mobile && flutter test`. Analyze: `flutter analyze lib`.
- Backend routes defined at root in `backend/api/main.py` (no `/api` prefix — proxy strips it for web).
- Commit footer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- The 9 category keys: `order_submit, order_fill, order_reject, order_retry, strategy_start, strategy_error, halt, drawdown_halt, crash_loop`.
- Run `gitnexus_impact` before editing backend symbols (live_alerts.*, robinhood adapter, outbox poller); index is stale + excludes Dart, so assess mobile by hand.

---

## Phase 1 — Fill-alert hardening (ships first, standalone)

### Task 1.1: Idempotency ledger for Robinhood fill alerts

**Files:**
- Modify: `backend/broker_adapters/robinhood.py` (`__init__` ~line 411-543 region; `_fire_fill_alert` ~2992)
- Test: `backend/tests/test_rh_fill_alert_idempotency.py`

- [ ] **Step 1 — failing test.** A `RobinhoodAdapter` (constructed with alert callback captured) fires `alert_order_fill` once per unique `broker_order_id`; a second `_fire_fill_alert` with the same `broker_order_id` is a no-op.

```python
# test: build adapter with a stub _alert_fill that appends to a list;
# call adapter._fire_fill_alert("AAPL","buy",1,100.0,"cid1","oid1") twice
# assert len(calls) == 1 and ("oid1" in adapter._alerted_fills)
```

- [ ] **Step 2 — run, expect FAIL** (`_alerted_fills` absent / fires twice).
- [ ] **Step 3 — implement.** In `__init__` add `self._alerted_fills: "OrderedDict[str, float]" = OrderedDict()` (bounded; cap 2048). In `_fire_fill_alert`, at top after the `None` guard:

```python
key = str(broker_order_id or cid or f"{symbol}:{side}")
if key in self._alerted_fills:
    return
self._alerted_fills[key] = filled_qty
if len(self._alerted_fills) > 2048:
    self._alerted_fills.popitem(last=False)
```

- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit** `fix(broker): idempotent RH fill alerts (dedup by broker_order_id)`.

### Task 1.2: Reconciliation-driven fill catch-up

**Files:**
- Modify: `backend/broker_adapters/robinhood.py` (add `_reconcile_fill_alerts`; call it at the end of `refresh_positions(force=True)` and in the terminal-fill `refresh_positions` call site ~2623)
- Test: `backend/tests/test_rh_fill_reconcile.py`

- [ ] **Step 1 — failing test.** Given a stubbed `self._client.get_recent_orders()` returning two `filled` orders `oid1`(already alerted) and `oid2`(not), `_reconcile_fill_alerts()` fires `alert_order_fill` exactly once — for `oid2` — and records it in `_alerted_fills`.
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement.** Add a method that pulls recent filled orders from the RH client (reuse whatever "recent orders" accessor exists; if none, query `self._client.get_orders()` filtered to `state=="filled"` within last 30 min by `updated_at`), and for each whose `id` is not in `_alerted_fills`, resolve symbol/side/qty/avg and call `self._fire_fill_alert(...)`. Wrap in try/except → log `BROKER` yellow on failure, never raise. Call it (best-effort) after the `refresh_positions(force=True)` in the terminal-fill branch and on the forced refresh path.

```python
def _reconcile_fill_alerts(self) -> None:
    try:
        recent = self._recent_filled_orders()  # helper: list of order dicts, state==filled
    except Exception as e:
        _alog("BROKER", f"fill-reconcile fetch failed: {e}", "yellow"); return
    for o in recent or []:
        oid = str(o.get("id") or "")
        if not oid or oid in self._alerted_fills:
            continue
        sym = (o.get("symbol") or "").upper() or self._instrument_url_to_symbol(o.get("instrument") or "")
        side = (o.get("side") or "").lower()
        try:
            q = float(o.get("cumulative_quantity") or 0.0)
            px = float(o.get("average_price") or 0.0)
        except Exception:
            continue
        if q > 0 and side in ("buy","sell"):
            cid = self._cid_for_broker_order_id(oid) or ""
            self._fire_fill_alert(sym, side, q, px, cid, oid)
```

- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit** `fix(broker): reconcile-driven fill-alert catch-up for missed fills`.

### Task 1.3: Outbox retry with backoff

**Files:**
- Modify: `backend/interactive_utils.py` (`action_get_pending_discord_messages` ~500; `action_mark_discord_message_failed` ~523; add `action_requeue_discord_message`)
- Modify: `backend/engines/discord_bot.py` (`_outbox_poller` failure branch ~1440-1446)
- Test: `backend/tests/test_outbox_retry.py`

- [ ] **Step 1 — failing test.** Unit-test the retry decision helper: `_retry_decision(attempts)` returns `("pending", next_retry_at)` while `attempts < MAX_ATTEMPTS(=5)` and `("failed", None)` at the cap; `action_get_pending_discord_messages` filters out rows whose `next_retry_at` is in the future. (Use a fake conn / monkeypatched rethink calls or factor the pure decision out for direct testing.)
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement.** Add a pure helper in `interactive_utils.py`:

```python
RETRY_MAX_ATTEMPTS = 5
def discord_retry_decision(attempts, now_ts):
    if attempts >= RETRY_MAX_ATTEMPTS:
        return ("failed", None, attempts)
    backoff = min(300, 5 * (2 ** attempts))  # 5,10,20,40,80,160→cap 300s
    return ("pending", now_ts + backoff, attempts + 1)
```

Update `action_get_pending_discord_messages` filter to also require `next_retry_at` absent or ≤ now (RethinkDB: `.filter(lambda d: (d['status']=='pending') & (~d.has_fields('next_retry_at') | (d['next_retry_at'] <= r.now().to_epoch_time())))`). In `discord_bot._outbox_poller`, on send failure call a new `action_requeue_or_fail_discord_message(conn, msg_id, error)` that reads current `attempts`, applies `discord_retry_decision`, and updates `{status, attempts, next_retry_at, error}`.

- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit** `fix(discord): retry failed/stuck outbox messages with backoff`.

### Task 1.4: Diagnostic fill-count logging

**Files:** Modify `backend/broker_adapters/robinhood.py` (`_polling_loop_inner` end-of-iteration ~2451)

- [ ] **Step 1 — implement** (low-risk log line; no test). After heartbeat bump, emit once per cycle when tracked orders exist:

```python
_alog("BROKER", f"RH poll cycle: pending_orders={len(tracked)} alerted_fills={len(self._alerted_fills)}", "cyan")
```

- [ ] **Step 2 — commit** `chore(broker): per-cycle fill diagnostics`.

**Phase 1 gate:** `cd backend && python -m pytest tests/test_rh_fill_alert_idempotency.py tests/test_rh_fill_reconcile.py tests/test_outbox_retry.py -v` → all pass.

---

## Phase 2 — Preferences store + routing layer (Discord-only default; no behavior change)

### Task 2.1: NotificationPreferences store helpers

**Files:** Modify `backend/interactive_utils.py`; Test `backend/tests/test_notification_prefs_store.py`

- [ ] **Step 1 — failing test.** `action_get_notification_preferences(conn, "u1")` returns all 9 categories defaulting `{discord: True, push: False}` when no doc exists; after `action_set_notification_preferences(conn, "u1", {...})`, a round-trip read returns the saved matrix; unknown category keys are rejected (raise `ValueError`).
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement.** Add module constant `NOTIFICATION_CATEGORIES = (...9 keys...)`, `ensure_notification_preferences_table(conn)` (mirror `ensure_agent_best_table`), and:

```python
def _default_notification_categories():
    return {c: {"discord": True, "push": False} for c in NOTIFICATION_CATEGORIES}

def action_get_notification_preferences(conn, user_id):
    ensure_notification_preferences_table(conn)
    doc = r.db(DB_NAME).table("NotificationPreferences").get(str(user_id)).run(conn)
    cats = _default_notification_categories()
    if doc and isinstance(doc.get("categories"), dict):
        for c, v in doc["categories"].items():
            if c in cats and isinstance(v, dict):
                cats[c] = {"discord": bool(v.get("discord", True)), "push": bool(v.get("push", False))}
    return {"user_id": str(user_id), "categories": cats}

def action_set_notification_preferences(conn, user_id, categories):
    ensure_notification_preferences_table(conn)
    clean = _default_notification_categories()
    for c, v in (categories or {}).items():
        if c not in clean:
            raise ValueError(f"unknown notification category: {c}")
        if isinstance(v, dict):
            clean[c] = {"discord": bool(v.get("discord", True)), "push": bool(v.get("push", False))}
    doc = {"id": str(user_id), "user_id": str(user_id), "categories": clean,
           "updated_at": datetime.now(timezone.utc).isoformat() + "Z"}
    r.db(DB_NAME).table("NotificationPreferences").insert(doc, conflict="replace").run(conn)
    return {"user_id": str(user_id), "categories": clean}
```

- [ ] **Step 4 — run, expect PASS. Step 5 — commit** `feat(notify): NotificationPreferences store + defaults`.

### Task 2.2: `notify()` routing layer (Discord sink only for now)

**Files:** Create `backend/notifications.py`; Test `backend/tests/test_notify_routing.py`

- [ ] **Step 1 — failing test.** With prefs mocked, `notify(category="order_fill", ...)`:
  - default prefs → calls discord sink exactly once with given channel/content/embed; push sink not called.
  - push-only prefs → push sink called, discord not.
  - both → both. neither → neither.
  - discord sink raising does not propagate and does not prevent push sink. (Inject sinks via module-level seams the test monkeypatches: `_discord_sink`, `_push_sink`, `_load_prefs`.)
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement.**

```python
"""Single fan-out point for live-trading notifications."""
from __future__ import annotations
from typing import Any, Optional
from intellistock_logger import intellistock_logger

def _load_prefs(user_id: Optional[str]) -> dict:
    from interactive_utils import get_conn, action_get_notification_preferences
    conn = get_conn()
    try:
        return action_get_notification_preferences(conn, user_id or _operator_user_id())
    finally:
        try: conn.close()
        except Exception: pass

def _operator_user_id() -> str:
    import os
    return os.environ.get("NOTIFY_OPERATOR_USER_ID", "operator")

def _discord_sink(channel, content, embed):
    from live_alerts import _safe_enqueue  # reuse redact/scrub + graceful-degrade
    _safe_enqueue(channel, content, embed=embed)

def _push_sink(user_id, *, title, body, category, data):
    try:
        from apns_sender import send_to_user
    except Exception:
        return
    send_to_user(user_id, title=title, body=body, category=category, data=data)

def notify(*, category, instance_id, title, body,
           discord_channel, discord_embed=None,
           push_title=None, push_body=None,
           user_id=None, data=None):
    try:
        prefs = _load_prefs(user_id).get("categories", {})
    except Exception as e:
        # fail-open to Discord (preserve today's behavior) if prefs unreadable
        intellistock_logger.log(f"notify: prefs load failed ({e}); discord fallback", "yellow", service="NOTIFY")
        prefs = {category: {"discord": True, "push": False}}
    route = prefs.get(category, {"discord": True, "push": False})
    if route.get("discord", True):
        try: _discord_sink(discord_channel, body, discord_embed)
        except Exception as e:
            intellistock_logger.log(f"notify discord sink failed: {e}", "yellow", service="NOTIFY")
    if route.get("push", False):
        try:
            _push_sink(user_id or _operator_user_id(),
                       title=push_title or title,
                       body=push_body or body,
                       category=category, data=data or {"category": category, "instance_id": instance_id})
        except Exception as e:
            intellistock_logger.log(f"notify push sink failed: {e}", "yellow", service="NOTIFY")
```

- [ ] **Step 4 — run, expect PASS. Step 5 — commit** `feat(notify): notify() routing layer (discord sink)`.

### Task 2.3: Refactor `live_alerts.py` to route through `notify()`

**Files:** Modify `backend/live_alerts.py` (9 `alert_*`); Test `backend/tests/test_live_alerts_parity.py`

- [ ] **Step 0 — impact.** `gitnexus_impact({target: "alert_order_fill", direction: "upstream"})` (+ others); confirm callers only import the functions (signatures unchanged → safe).
- [ ] **Step 1 — failing parity test.** Monkeypatch `notifications.notify`; call each `alert_*` with sample args; assert `notify` was called once with the expected `category`, `discord_channel` (e.g. `halt`→`notifications`, `order_fill`→`_channel()`), and the SAME `discord_embed`/`body` the old code produced. Also assert default-prefs path still results in exactly one `_safe_enqueue` (integration: monkeypatch `_safe_enqueue`, real `notify`, default prefs).
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement.** In each `alert_*`, keep building `content` + `embed`, replace the trailing `_safe_enqueue(channel, content, embed=embed)` with:

```python
from notifications import notify
notify(category="order_fill", instance_id=instance_id,
       title=f"Filled {symbol}", body=content,
       discord_channel=_channel(), discord_embed=embed,
       push_body=f"{side.upper()} {filled_qty:.4f} {symbol} @ ${filled_avg_price:.2f}")
```

(Map each function to its category + existing channel: `halt`/`crash_loop`→`_channel("notifications")`, `drawdown_halt`→`os.environ.get("LIVE_TRADES_CHANNEL","trades")`, rest→`_channel()`.) Keep `_safe_enqueue` as the discord sink (do not delete).

- [ ] **Step 4 — run parity test, expect PASS. Step 5 — commit** `refactor(alerts): route live alerts through notify() (discord parity preserved)`.

### Task 2.4: Preferences API endpoints

**Files:** Modify `backend/api/main.py`; Test `backend/tests/test_api_notification_prefs.py` (FastAPI `TestClient`)

- [ ] **Step 1 — failing test.** With auth dependency overridden to a fake user, `GET /notification-preferences` returns 9 categories with defaults; `PUT` with a valid matrix persists and round-trips; `PUT` with an unknown category → 400.
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement.** Add Pydantic body + two endpoints mirroring the `/tickers` pattern:

```python
class CategoryRoute(BaseModel):
    discord: bool = True
    push: bool = False
class NotificationPrefsBody(BaseModel):
    categories: Dict[str, CategoryRoute]

@app.get("/notification-preferences", response_class=JSONResponse)
def api_get_notification_prefs(conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    return _run(action_get_notification_preferences, conn, current_user["id"])

@app.put("/notification-preferences", response_class=JSONResponse)
def api_put_notification_prefs(body: NotificationPrefsBody, conn=Depends(conn_dependency), current_user: dict = Depends(get_current_user)):
    cats = {k: v.dict() for k, v in body.categories.items()}
    try:
        return _run(action_set_notification_preferences, conn, current_user["id"], cats)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

Import the two actions at top of `main.py`.

- [ ] **Step 4 — run, expect PASS. Step 5 — commit** `feat(api): GET/PUT /notification-preferences`.

---

## Phase 3 — APNs sender + device registration

### Task 3.1: Add httpx[http2] dependency

**Files:** Modify `backend/requirements.txt`

- [ ] **Step 1 — implement.** Append `httpx[http2]>=0.27` (pulls in `h2`). Run `cd backend && pip install 'httpx[http2]'` to confirm install. **Commit** `chore(deps): add httpx[http2] for APNs`.

### Task 3.2: PushDevices store helpers

**Files:** Modify `backend/interactive_utils.py`; Test `backend/tests/test_push_devices_store.py`

- [ ] **Step 1 — failing test.** `action_register_push_device(conn, user_id, token, platform="ios", env="prod", app_version=...)` upserts keyed by token (idempotent, refreshes `last_seen`); `action_list_push_devices(conn, user_id, env=...)` returns the user's devices; `action_delete_push_device(conn, token)` removes it.
- [ ] **Step 2 — run, expect FAIL. Step 3 — implement** `ensure_push_devices_table` + the three actions (id == token, `conflict="replace"`). **Step 4 — pass. Step 5 — commit** `feat(notify): PushDevices store`.

### Task 3.3: APNs sender module

**Files:** Create `backend/apns_sender.py`; Test `backend/tests/test_apns_sender.py`

- [ ] **Step 1 — failing tests** (httpx mocked — no real Apple):
  - JWT builder produces an ES256 token with `kid`/`iss` header/claims and is cached (<2 calls within window).
  - `send_to_user` with creds-absent → no-op returns `{"sent":0,"skipped":...}` and makes no HTTP call.
  - With one device + mocked 200 → POST to `/3/device/<token>` with `apns-topic == APNS_BUNDLE_ID`, payload `{aps:{alert:{title,body},sound:"default"}, category, ...data}`.
  - Mocked `410` → device pruned via `action_delete_push_device`.
- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement.** Config from env/secret_store; `_load_creds()` returns None if incomplete → `send_to_user` no-ops (log once). JWT via PyJWT `jwt.encode({"iss":team,"iat":now}, key, algorithm="ES256", headers={"kid":key_id})`, cached ~50 min. `httpx.Client(http2=True)` to `https://api.push.apple.com` (or sandbox). Iterate `action_list_push_devices`; POST per token with headers `apns-topic`, `apns-push-type:"alert"`, `authorization: bearer <jwt>`; on 410/`BadDeviceToken` call `action_delete_push_device`. Never raise.
- [ ] **Step 4 — run, expect PASS. Step 5 — commit** `feat(notify): direct APNs sender (token-based, no Firebase)`.

### Task 3.4: Device registration API

**Files:** Modify `backend/api/main.py`; Test `backend/tests/test_api_push_devices.py`

- [ ] **Step 1 — failing test.** `POST /push/devices` with `{device_token, platform:"ios", env, app_version}` registers and returns ok; `DELETE /push/devices/{token}` removes; both require auth.
- [ ] **Step 2 — FAIL. Step 3 — implement** mirroring `/tickers` (body model `PushDeviceBody`; `current_user["id"]`). **Step 4 — pass. Step 5 — commit** `feat(api): POST/DELETE /push/devices`.

**Phase 3 gate:** all backend pytest green; `notify` with push-enabled pref + a registered device + mocked APNs delivers (integration test in `test_notify_routing.py`).

---

## Phase 4 — Mobile iOS push

### Task 4.1: Add flutter_local_notifications dep

**Files:** Modify `mobile/pubspec.yaml`

- [ ] **Step 1 — implement.** Add `flutter_local_notifications: ^18.0.1`; `cd mobile && flutter pub get`. **Commit** `chore(mobile): add flutter_local_notifications`.

### Task 4.2: Native iOS APNs token bridge

**Files:** Modify `mobile/ios/Runner/AppDelegate.swift`, `mobile/ios/Runner/Info.plist`, `mobile/ios/Runner.xcodeproj` capabilities (push + background remote-notification)

- [ ] **Step 1 — implement AppDelegate.** Keep the implicit-engine setup; add: in `didFinishLaunchingWithOptions` set `UNUserNotificationCenter.current().delegate = self` and call `application.registerForRemoteNotifications()` after Dart requests it (gate behind a MethodChannel call from Dart so we don't prompt before login). Implement `didRegisterForRemoteNotificationsWithDeviceToken` → hex-encode token → send on `MethodChannel("intellistock/push")` via `invokeMethod("onToken", hexToken)`. Create the channel using the implicit engine's binary messenger (store a reference in `didInitializeImplicitFlutterEngine`). Add `requestAuthorization`+`registerForRemoteNotifications` handler for an incoming `registerPush` method call.
- [ ] **Step 2 — Info.plist:** add `UIBackgroundModes` → `remote-notification`. Add `aps-environment` via entitlements (`Runner.entitlements`: `development` for debug, `production` release) and reference it in the project. Document that the operator must enable Push Notifications capability in the Apple Developer portal for `dev.pkrishna.intellistockMobile`.
- [ ] **Step 3 — commit** `feat(mobile/ios): APNs token bridge via MethodChannel`.

### Task 4.3: Dart push service

**Files:** Create `mobile/lib/core/push/push_service.dart`, `mobile/lib/core/push/push_repository.dart`; Test `mobile/test/core/push/push_repository_test.dart`

- [ ] **Step 1 — failing test.** `PushRepository.registerToken(token)` POSTs `/push/devices` with `{device_token, platform:"ios", env, app_version}` (mock `ApiClient`); `unregister(token)` DELETEs.
- [ ] **Step 2 — FAIL. Step 3 — implement** repository (mirror ChatbotRepository) + `PushService` (Notifier or plain class) that: listens on `MethodChannel("intellistock/push")` for `onToken` → calls `registerToken`; exposes `enable()` which invokes `registerPush` on the channel (triggers native permission + registration); initializes `flutter_local_notifications` for foreground display. Wire `enable()` to run after successful login (in the session/bootstrap flow) and only on iOS.
- [ ] **Step 4 — pass; `flutter analyze lib`. Step 5 — commit** `feat(mobile): iOS push service + device registration`.

### Task 4.4: Notification settings — state + repository

**Files:** Create `mobile/lib/features/settings/data/notification_prefs_repository.dart`, `mobile/lib/features/settings/application/notification_prefs_notifier.dart`, model `mobile/lib/features/settings/domain/notification_prefs.dart`; Test `mobile/test/features/settings/notification_prefs_notifier_test.dart`

- [ ] **Step 1 — failing test.** Notifier loads prefs (9 categories) from repo; `toggle(category, channel, value)` optimistically updates state and PUTs; on PUT failure reverts and sets error. (Mock repository.)
- [ ] **Step 2 — FAIL. Step 3 — implement.** Model = `{Map<String, ({bool discord, bool push})> categories}` (or freezed). Repository GET `/notification-preferences`, PUT full matrix. Notifier `extends Notifier<NotificationPrefsState>` + `NotifierProvider`, mirroring `ChatbotNotifier`. **Step 4 — pass. Step 5 — commit** `feat(mobile): notification prefs state + repository`.

### Task 4.5: Notification settings screen + nav

**Files:** Create `mobile/lib/features/settings/presentation/notification_settings_screen.dart`; Modify `mobile/lib/core/router/router.dart` (+route `/settings/notifications`), and the settings entry (link from `SettingsScreen` or `more_sheet.dart`); Test `mobile/test/features/settings/notification_settings_screen_test.dart`

- [ ] **Step 1 — failing widget test.** Pump the screen with an overridden provider → finds 9 category rows, each with two `Switch.adaptive` (Discord, push); toggling calls the notifier.
- [ ] **Step 2 — FAIL. Step 3 — implement** the screen (grouped list, two switches per row, labels from a category→display map; loading/error states) + register route + add a "Notifications" entry in `SettingsScreen`. Use `symbol('notifications')` if present in material_symbols map (else a present icon). **Step 4 — pass; `flutter analyze lib`; `flutter test`. Step 5 — commit** `feat(mobile): notification settings screen`.

---

## Phase 5 — Web settings view

### Task 5.1: NotificationSettingsView + route + nav

**Files:** Create `frontend/src/views/NotificationSettingsView.vue`; Modify `frontend/src/router/index.js`, `frontend/src/layouts/AppShell.vue` (navItems)

- [ ] **Step 1 — implement view.** `<script setup>` with `preferences` ref `{categories:{}}`, `fetchPreferences()` GET `/notification-preferences`, `savePreferences()` PUT full matrix, `authHeaders()` from `utils/auth.js`, `onMounted(fetchPreferences)`. Template: wrap in `<AppShell>`; render a table/grid of the 9 categories (friendly labels) × two toggles (Discord, iOS push) using the BrokeragesView toggle-button pattern, bound to `preferences.categories[key].discord/.push`; Save button + status message. Loading/error states.
- [ ] **Step 2 — register route** `{ path: '/notification-settings', name: 'notification-settings', component: () => import('../views/NotificationSettingsView.vue'), meta: { requiresAuth: true } }` and add a `navItems` entry `{ label: 'Notifications', icon: 'notifications', to: '/notification-settings' }`.
- [ ] **Step 3 — verify build.** `cd frontend && npm run build` (no test runner) → compiles clean. **Step 4 — commit** `feat(web): notification settings view`.

---

## Final verification (before bug sweep)
- `cd backend && python -m pytest tests/ -q` (new tests green; no regressions in touched areas).
- `cd mobile && flutter analyze lib && flutter test` (clean + all pass).
- `cd frontend && npm run build` (clean).
- Manual/operator: APNs `.p8` creds + on-device delivery (sandbox) — documented, done by user.

## Spec coverage check
- §1 routing/prefs → Tasks 2.1–2.4. §2 APNs/devices → 3.1–3.4. §3 hardening → 1.1–1.4. §4 mobile → 4.1–4.5. §5 web → 5.1. Shared API → 2.4 + 3.4. Discord parity → 2.3 parity test. ✔ all covered.
