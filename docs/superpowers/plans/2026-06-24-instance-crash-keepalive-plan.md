# Implementation plan — instance crash keep-alive + crash notification

Spec: `docs/superpowers/specs/2026-06-24-instance-crash-keepalive-design.md`
Branch: `feat/instance-crash-keepalive` (off `origin/main`).
Approach: TDD per task (red → green), backend first, then web, then mobile, then bug sweep + push.

Impact analysis (gitnexus, run pre-edit):
- `start_broker` LOW (2 in-file callers), `terminate_thread_on_command` LOW (1 caller),
  `default_routing` LOW (2 callers + 1 test), `action_instances` HIGH rating but the edit is
  purely additive (append-only) → no consumer breaks.

## Task 1 — Notification category `instance_crash` (`backend/notification_types.py`)
- Add `instance_crash` entry to `NOTIFICATION_TYPES` after `crash_loop` (Risk & Halts group,
  channel `notifications`, prefix `"INSTANCE CRASH ["`, discord+push True).
- Add `_PUSH_ON_BY_DEFAULT = {"instance_crash"}`; `default_routing()` sets
  `"push": t["key"] in _PUSH_ON_BY_DEFAULT`.
- Tests (`tests/test_notification_types.py`):
  - UPDATE `test_default_routing_covers_all_keys_discord_on_push_off` → assert discord True
    for all; push False for all **except** `instance_crash`; and `instance_crash` push True.
  - ADD: `instance_crash` is a valid key; `classify(content="INSTANCE CRASH [x] ...")` →
    `"instance_crash"`.

## Task 2 — `alert_instance_crash()` (`backend/live_alerts.py`)
- Mirror `alert_crash_loop`: `alert_instance_crash(*, instance_id, reason, detail="")`.
  Content `INSTANCE CRASH [<id>] <reason>`; embed title "Instance crashed" + reason/detail
  fields (truncate detail); `notify(category="instance_crash", discord_channel=
  _channel("notifications"), push_body=<reason>)`. Best-effort, never raises.
- Tests (`tests/test_live_alerts_parity.py`): add a parametrized case → category
  `instance_crash`, channel `notifications`; plus a content/prefix assertion.

## Task 3 — Crash keep-alive guard (`backend/instance.py`) — core
New, testable units (module-level, mockable):
- `CHANGEFEED_RETRY_MAX` const (default 3) + `_changefeed_backoff(attempt)`.
- `_keepalive_enabled(kind)` → `kind != 'kalshi'`; module global `_KEEPALIVE_ENABLED` set in `run()`.
- `_operator_stop_requested(conn, instance_id)` → True iff row `runCommand is False`.
- `_write_crash_to_logfile(instance_id, text)` → append (mode "a", no rotation) via
  `live_state.log_file_path_for`; best-effort.
- `_mark_instance_crashed(instance_id, reason)` → best-effort row update
  `{crashed:True, crashed_at:r.now(), running:True, status:'crashed'}`.
- `enter_crash_keepalive(reason, exc=None)` — idempotent (`_crash_lock`/`_crash_entered`):
  terminate broker; log+append traceback; mark crashed; `alert_instance_crash`; then block
  (`_block_until_operator_stop()` — loop sleep(2), `os._exit(0)` only on operator stop).
- `_block_until_operator_stop()` split out so the block is testable without真正 sleeping
  forever (inject a max-iteration / stop hook in tests).

Wiring (each new branch gated by `_KEEPALIVE_ENABLED`):
- `terminate_thread_on_command` / `run_instance_stocks_changefeed`: on changefeed error,
  retry reconnect up to `CHANGEFEED_RETRY_MAX` with backoff; exhausted → equities
  `enter_crash_keepalive(...)`, else current `os._exit(0)`. `runCommand=False` branch unchanged.
- `start_broker` latch (after `alert_crash_loop`): equities → `enter_crash_keepalive(
  "broker crash-loop latched")`; else current `return`.
- `run()`: load `kind`, set `_KEEPALIVE_ENABLED`; wrap supervisor loop body so any exception →
  `enter_crash_keepalive`; break loop if `_crash_entered`. Startup DB-connect failure: equities
  best-effort `enter_crash_keepalive` else `os._exit(1)`.
- `__main__`: wrap `run()` in `try/except BaseException` → if keepalive enabled,
  `enter_crash_keepalive`; else re-raise.
- Operator Stop (`runCommand=False`, `:88`) and socket `terminate` (`:294`) stay `os._exit(0)`.
- Daemon-thread crashes call `enter_crash_keepalive` (idempotent) AND the main loop observes
  `_crash_entered` and itself blocks (so the broker stays terminated process-wide).
- Tests (`tests/test_instance_crash_handling.py`, new): patch `os._exit`, DB conn, broker
  proc, `alert_instance_crash`. Cover — `enter_crash_keepalive` idempotency; equities exhausted
  changefeed → keepalive (no `os._exit`); kalshi → `os._exit(0)`; `runCommand=False` still
  exits; `_keepalive_enabled` gate; `_operator_stop_requested` truth table; latch → keepalive.

## Task 4 — Expose `crashed` in instances payload (`backend/interactive_utils.py`)
- `action_instances` (~1076): append `"crashed"` to `.pluck(...)`; add
  `"crashed": row.get("crashed", False)` to the row dict (additive).
- `action_get_instance` (~1439): add `"crashed": ... , "crashed_at": ...` (additive).
- Test: extend an existing instances-payload test (or add one) asserting `crashed` defaults
  False and surfaces True when set. (Mock conn returns a row with `crashed=True`.)

## Task 5 — Web crashed badge (`frontend/`)
- `InstancesView.vue` (~1050) + `InstanceDetailView.vue` (~1292): badge shows
  CRASHED (red, no pulse) when `inst.crashed`, else Running/Stopped as today.
- `NotificationSettingsView.vue` FALLBACK_TYPES (~11): add `instance_crash` entry.
- Verify with `vite build`.

## Task 6 — Mobile crashed badge + settings (`mobile/`)
- `instances/data/models/instance.dart`: add `crashed` (bool), `crashedAt` (String?) to ctor,
  fields, `fromJson` (`j['crashed'] == true`), and `copyWith`.
- `instances_screen.dart` (~447) + `instance_detail_screen.dart` (~420): StatusPill →
  CRASHED (AppColors.danger, not pulsing) when `inst.crashed`.
- `settings/data/models/notification_prefs.dart` kNotificationCategories (~112): add
  `instance_crash` entry.
- Verify with `flutter analyze`.

## Task 7 — Verify + bug sweep + push
- Run backend tests (notification/alerts/instance + full suite sanity), `flutter analyze`,
  `vite build`. `gitnexus detect_changes` pre-commit.
- Parallel adversarial bug sweep (subagents) over the diff; fix real findings.
- Commit (logical commits) and push the branch. No PR unless asked.

## Risks / watch-items
- Blocking forever must NEVER trigger on operator Stop — assert in tests.
- `enter_crash_keepalive` must be best-effort throughout (DB down is the common trigger) —
  every external call wrapped, never raises out of the handler except the intended block.
- Don't rotate the live log (use append) — rotation would discard the crash tail.
- `default_routing` change must not flip push-on for any existing category — regression test.
