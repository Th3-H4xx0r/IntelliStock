# Per-category notification routing + iOS push + fill-alert hardening

**Date:** 2026-06-10
**Branch:** `feat/notifications-routing-push` (off `feat/mobile-chart-scrubbing`)
**Status:** Approved — implementing end-to-end

## Problem

Two problems, one feature:

1. **Bug.** In a recent live run, 8 assets were bought but only **1** "buy filled" Discord
   notification arrived. The other 7 fills went silent.
2. **No routing control.** Every live-trading alert is hardcoded to Discord
   (`live_alerts.py` → `_safe_enqueue` → `enqueue_discord_message`). There is no way to
   choose, per category of notification, whether it goes to Discord, to a phone push, or
   both. There is no mobile push capability and no settings UI on either client.

## Goals

- Fix the *class* of failure behind "1 of 8 fills notified" so fills are reliably
  reported even when the broker polling loop misses one. (Run logs are server-side; we
  harden defensively rather than chase one incident.)
- A **routing layer**: for each of the 9 notification categories, route to Discord
  and/or iOS push, independently.
- **iOS push** via direct **APNs** (token-based, no Firebase).
- **Settings UI** on both the Flutter app and the Vue web frontend to configure the
  per-category routing matrix. Web is **config-only** — no web delivery.

## Non-goals

- Android push / FCM (parked — iOS only for now; keep a clean seam for later).
- Web/browser push delivery (the web app only *configures* preferences).
- In-app notification feed/center (out of scope; push + Discord are the sinks).
- Multi-tenant fan-out — system is effectively single-operator (`main` = Robinhood real
  money). Preferences are stored per-user but only the operator's matter today.

## The 9 notification categories

From `backend/live_alerts.py` (kept 1:1 — user chose "all 9 individually"):

| key | function | meaning |
|-----|----------|---------|
| `order_submit`   | `alert_order_submit`   | order submitted to broker |
| `order_fill`     | `alert_order_fill`     | order filled |
| `order_reject`   | `alert_order_reject`   | order rejected (final) |
| `order_retry`    | `alert_order_retry`    | order retried after recoverable reject |
| `strategy_start` | `alert_strategy_start` | strategy first run of session |
| `strategy_error` | `alert_strategy_error` | unrecoverable strategy error |
| `halt`           | `alert_halt`           | manual halt |
| `drawdown_halt`  | `alert_drawdown_halt`  | drawdown risk-off tripped |
| `crash_loop`     | `alert_crash_loop`     | broker subprocess crash loop |

## Architecture

```
live_alerts.alert_*()  ──build embed/event──▶  notifications.notify(category, …)
                                                     │
                                   read NotificationPreferences[user][category]
                                                     │
                          ┌──────────────────────────┴───────────────────────────┐
                   discord enabled?                                        push enabled?
                          │                                                       │
              enqueue_discord_message  (unchanged)              apns_sender.send_to_user(user, …)
              → DiscordOutbox → bot                              → PushDevices(ios) → api.push.apple.com
```

### 1. Backend — routing & preferences

**New module `backend/notifications.py`** — the single fan-out point:

```python
def notify(
    *, category: str, instance_id: str,
    title: str, body: str,
    fields: list[dict] | None = None,
    color: int | None = None,
    discord_channel: str | None = None,   # override; default per-category
    discord_embed: dict | None = None,    # pre-built embed (alerts already build these)
    user_id: str | None = None,           # whose prefs/devices; default = operator
) -> None
```

`notify`:
1. Resolves preferences for `(user_id, category)` (default if absent).
2. If `discord` enabled → `enqueue_discord_message(...)` exactly as today
   (reuses the existing redact/scrub + graceful-degrade behavior — see "Discord parity").
3. If `push` enabled → `apns_sender.send_to_user(user_id, title=title, body=body, category=category, data={...})`.
4. Each sink is wrapped so one failing never blocks trading or the other sink (mirrors
   today's `_safe_enqueue` try/except + log line).

**Preferences storage — RethinkDB table `NotificationPreferences`:**

```
{ id: "<user_id>", user_id, categories: { <category>: {discord: bool, push: bool}, ... },
  updated_at }
```

One doc per user (id == user_id), categories as a nested map → a single read per notify,
single write per settings save. Helper layer in `interactive_utils.py`:
`action_get_notification_prefs(conn, user_id)`, `action_set_notification_prefs(conn, user_id, categories)`,
plus `ensure_notification_prefs_table(conn)`.

**Defaults (behavior-preserving):** every category `{discord: true, push: false}`. Until
the operator opts a category into push, behavior is byte-for-byte identical to today.

**`live_alerts.py` refactor:** each `alert_*` keeps building its `content` + `embed`, then
calls `notify(category="order_fill", instance_id=…, title=…, body=content, discord_embed=embed, discord_channel=_channel())`
instead of `_safe_enqueue(...)`. The push `title`/`body` are short human strings derived
from the same data. `_safe_enqueue` stays (used by `notify`'s discord sink) so the
redact/scrub path is unchanged.

### Discord parity (must not regress real-money alerts)

- The discord sink in `notify` calls the **existing** `_safe_enqueue` (redact + `_scrub_embed`
  + graceful-degrade + the cyan/yellow log line). No new Discord code path.
- Channel routing unchanged: per-category default channel preserved (e.g. `halt`/`crash_loop`
  → `notifications`, others → `_channel()` = `trades`/`LIVE_ALERTS_CHANNEL`).
- With default prefs, `notify` ≡ today's single `_safe_enqueue` call.

### 2. Backend — iOS push (direct APNs)

**New module `backend/apns_sender.py`** — token-based APNs over HTTP/2:

- Auth: ES256 JWT signed with a `.p8` key. Config via env / `secret_store`:
  `APNS_KEY_ID`, `APNS_TEAM_ID`, `APNS_BUNDLE_ID`, `APNS_KEY_PATH` (or key material in
  secret_store), `APNS_ENV` (`sandbox`|`prod`, default `prod`).
- Transport: `httpx` client with HTTP/2 (`http2=True`). Endpoints
  `api.push.apple.com` / `api.sandbox.push.apple.com`. JWT cached ~50 min (Apple allows
  1/per-20-min refresh, ≤60 min lifetime).
- `send_to_user(user_id, *, title, body, category, data)`:
  look up `PushDevices` for user (platform=ios, matching env) → POST per token →
  on `410`/`BadDeviceToken` prune the token. Returns counts; never raises to caller.
- **Disabled-safe:** if APNs creds are absent, module logs once and `send_to_user` no-ops
  (push simply doesn't deliver; Discord unaffected).

**Device registry — RethinkDB table `PushDevices`:**

```
{ id: "<device_token>", user_id, device_token, platform: "ios",
  env: "sandbox"|"prod", app_version, created_at, last_seen }
```

`id == device_token` makes register idempotent (upsert refreshes `last_seen`).

### 3. Backend — fill-alert hardening (the "1 of 8" fix)

Root cause is unprovable from local data (server logs), so fix the failure *class*:

1. **Idempotency ledger.** `RobinhoodAdapter._alerted_fills: set[str]` keyed by
   `broker_order_id`. `_fire_fill_alert` records the id; both the polling path and the new
   reconciliation path consult it → each order's fill alert fires **exactly once** (no
   double, no drop). Bounded (popped with the order; capped LRU as a backstop).
2. **Reconciliation-driven catch-up.** After `refresh_positions(force=True)`, scan RH's
   recent *filled* orders (order history, last ~30 min) and fire `_fire_fill_alert` for any
   filled order **not** in `_alerted_fills`. This is the real safety net — it reports fills
   the 60s polling loop never observed (e.g. RH rate-limiting the rapid post-submit
   `get_order` burst, the most plausible cause of 7 silent fills).
3. **Outbox retry.** `discord_bot._outbox_poller` currently fetches only `status=="pending"`
   and marks failures `"failed"` forever. Add bounded retry: on failure set
   `status="pending"`, `attempts += 1`, `next_retry_at = now + backoff` until
   `MAX_ATTEMPTS` (then `"failed"`); `action_get_pending_discord_messages` skips rows whose
   `next_retry_at` is in the future. A transient send error now self-heals.
4. **Diagnostic logging.** Polling loop emits a per-cycle
   `fills_detected=N alerted=N pending_orders=N` line (service `BROKER`) so the next
   incident is pinpointable from logs.

These are independent of routing and ship first.

### 4. Mobile (Flutter) — push + settings

**Push registration (no Firebase):**
- iOS `AppDelegate.swift`: `registerForRemoteNotifications()`; on
  `didRegisterForRemoteNotificationsWithDeviceToken` send the hex token to Dart over a
  `MethodChannel("intellistock/push")`.
- Dart `core/push/push_service.dart`: request permission (`UNUserNotificationCenter`
  authorization), receive token, `POST /api/push/devices`. Presents foreground alerts via
  `flutter_local_notifications`; tap routes to the relevant screen via go_router.
- `Info.plist`: add `aps-environment` (`development` for debug builds, `production` for
  release) + background-modes `remote-notification`.
- Add deps: `flutter_local_notifications`. APNs token obtained natively (no
  `firebase_messaging`).

**Notification settings screen** (`features/settings/presentation/notification_settings_screen.dart`):
- 9 category rows, each with two switches: **Discord** and **iOS push**.
- Riverpod provider over `GET/PUT /api/notification-preferences`; optimistic toggle
  (revert on failure), following the existing `ChatSettingsSheet` pattern.
- Reachable from the app's settings entry point.

### 5. Web (Vue frontend) — settings only

**`NotificationSettings` view** (`frontend/src/views/...` + store):
- Same 9 × {Discord, iOS push} matrix; same `GET/PUT /api/notification-preferences`.
- **No delivery** — purely configuration from a desktop browser.
- Follows existing view/store/router patterns (verified during planning).

### Shared API (`backend/api/`)

| method | path | purpose |
|--------|------|---------|
| GET | `/api/notification-preferences` | current user's matrix (defaults filled in) |
| PUT | `/api/notification-preferences` | replace the matrix |
| POST | `/api/push/devices` | register/refresh an iOS device token |
| DELETE | `/api/push/devices/{token}` | unregister (logout / disable) |

Auth: existing JWT middleware; `user_id` from the token. Validation: category keys must be
in the known set; `discord`/`push` are booleans.

## Data flow (fill, push+discord enabled)

1. RH polling detects `order_fill` (or reconciliation catch-up does).
2. `_fire_fill_alert` (idempotent) → `alert_order_fill(...)`.
3. `alert_order_fill` builds embed → `notify(category="order_fill", …)`.
4. `notify` reads prefs → Discord enqueue **and** `apns_sender.send_to_user`.
5. Bot drains outbox → Discord; APNs delivers to the phone (even if app closed).

## Error handling

- Every sink call is isolated; a sink failure logs and continues (trading never blocks).
- APNs `410`/bad-token prunes the device row.
- Missing APNs creds → push globally no-ops (logged once); Discord unaffected.
- Settings API validates category keys + boolean values; rejects unknown categories.
- Optimistic UI reverts on PUT failure.

## Testing

- **Backend (pytest, primary):**
  - `notify` routing matrix: discord-only / push-only / both / neither (mock both sinks).
  - Preferences store: defaults, round-trip, unknown-category rejection.
  - APNs sender: JWT build/caching, payload shape, `410` prunes token, creds-absent no-op
    (httpx mocked — no real Apple calls).
  - Idempotency ledger: same `broker_order_id` alerts once across polling+reconciliation.
  - Reconciliation catch-up: filled-but-unalerted order → fires; already-alerted → skipped.
  - Outbox retry: failed row re-queued with backoff, gives up after `MAX_ATTEMPTS`.
  - **Real-money parity:** default prefs → `notify` makes exactly one `_safe_enqueue` call
    with the same channel/content/embed as the pre-refactor alert.
- **Mobile (flutter test):** preferences provider (load/toggle/optimistic-revert);
  settings screen renders 9 rows with two switches each; push_service token POST (mocked
  Dio). Native AppDelegate not unit-tested.
- **Web (vitest/existing):** settings store load/save; view renders the matrix.
- **Manual (operator):** on-device APNs delivery (sandbox build) — requires real `.p8`
  creds + device, done by the user.

## Rollout / ops notes

- APNs creds: operator adds `.p8` + `APNS_KEY_ID`/`APNS_TEAM_ID`/`APNS_BUNDLE_ID` to backend
  env (documented in the plan). Absent ⇒ push disabled, no errors.
- New RethinkDB tables auto-created via `ensure_*` helpers on first use (matches existing
  `DiscordOutbox` pattern).
- Defaults preserve current Discord behavior; push is strictly opt-in per category.

## Build order (each independently verifiable)

1. **Fill-alert hardening** (§3) — standalone, ships first, addresses the immediate pain.
2. **Preferences + routing layer** (§1) with Discord-only defaults (no behavior change).
3. **APNs sender + device registration** (§2) + settings/devices API (§shared API).
4. **Mobile push** (§4 push).
5. **Mobile + Web settings UIs** (§4 settings, §5).

## Risks

- `live_alerts.py` is on the **real-money** alert path — mitigated by behavior-preserving
  defaults + an explicit parity test.
- GitNexus index does not cover Dart; mobile impact assessed by hand.
- Branch is layered on `feat/mobile-chart-scrubbing` (not yet merged); PR targets `main`
  and rebases if the chart branch merges first.
