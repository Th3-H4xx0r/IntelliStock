# Instance crash keep-alive + crash notification — design

Date: 2026-06-24
Branch target: a new feature branch off `main` (do not build on the Kalshi branch).
Status: approved design (decisions captured via brainstorming Q&A).

## Problem

The real-money equities instance `alpaca-main` (and other equities instances) sometimes
dies and its container stops with no notification, and the operator cannot see the logs
that explain why. The operator wants: when an instance dies for any reason **other than
the operator pressing Stop**, the process must **not** exit — it should send a configurable
crash notification and then **block indefinitely** so the container stays up and the logs
remain viewable in logs-history.

## How instances run today (facts established by exploration)

- Container entrypoint for **both** equities and Kalshi instances is
  `backend/instance.py::run()` (launched as `python instance.py <id>`). The **container
  stops when this process exits.**
- `run()` spawns the trading child via `start_broker()`:
  - equities → `python broker.py <id> live ...`
  - Kalshi → `python -m kalshi.runner <id>` (dispatched on the Instances row `kind == 'kalshi'`)
- A supervisor loop (`instance.py:383-393`) restarts the broker if it exits. A broker
  crash-loop (>5 restarts / 60s) **latches** (`start_broker`, `:132-164`): it stops
  restarting, records `status='crash-looping'`, fires `alert_crash_loop`, and `return`s —
  so the container stays alive but the supervisor then logs "not restarting" every 2s.
- The paths that actually **stop the container without notice**:
  1. `os._exit(0)` in the two changefeed-error handlers — `terminate_thread_on_command`
     (`:89-92`) and `run_instance_stocks_changefeed` (`:256-259`). A *transient* RethinkDB
     changefeed blip hard-exits the whole process. **Most likely cause of the random stops.**
  2. An unhandled exception escaping `run()` / the supervisor loop.
  3. Startup DB-connect failure (`:296-299`, `os._exit(1)`).
- **Legit stops that must keep exiting:** operator Stop → `runCommand=False` (`:82-88`,
  `os._exit(0)`), and the server-emitted socket `terminate` event (`:311-315`).
- Notifications already work from inside the instance container: `instance.py` already calls
  `alert_crash_loop()` (`:160-161`), which enqueues to the `DiscordOutbox` RethinkDB table;
  the server's Discord poller delivers to Discord and/or iOS push.
- Logs: the broker child writes `instance_<id>.log` **line-buffered** into the shared Docker
  volume `live_trading_logs`. The logs-history endpoint
  (`GET /instances/{id}/live-logs` → `action_live_trading_logs`) reads that file. On the
  next instance start, the broker **archives** the old log under a timestamped name and
  opens a fresh one — which is why a stop-then-restart makes the crash log "disappear" from
  the UI. Keeping the process alive (never restarting/archiving) keeps the crash log visible.

## Decisions (from brainstorming)

1. **Scope:** all **equities** instances (any `instance.py`-launched instance with
   `kind != 'kalshi'`). Kalshi keeps its own `kalshi/runner.py` crash handling.
2. **Coverage:** every non-operator termination — unhandled exceptions **and** the `os._exit`
   changefeed paths. Only operator Stop (and the socket `terminate` event) still exit.
3. **Transient blips:** the changefeed loops **retry ~3 reconnects with short backoff**
   before declaring a crash, so a brief DB blip self-heals; a persistent failure → crash.
4. **Notification default:** new `instance_crash` category, **Discord + iOS push both ON by
   default**, configurable per-channel in the settings notifications screen.
5. **Dashboard:** mark the Instances row `crashed=true` + `crashed_at` (with `running=true`
   so the container is not reaped) so the UI can show a "crashed" badge.
6. **Indefinite block:** the keep-alive blocks forever; it exits **only** on operator Stop
   (`runCommand=False`). No signal trapping (see "Honest limit" below).

## Components

### 1. Notification category `instance_crash` (`backend/notification_types.py`)
- Add one entry to `NOTIFICATION_TYPES` (in the "Risk & Halts" group, after `crash_loop`):
  `{"key": "instance_crash", "group": "Risk & Halts", "label": "Instance crashed",
    "desc": "A trading instance process died and was held open for log capture",
    "channel": "notifications", "discord": True, "push": True,
    "prefixes": ["INSTANCE CRASH ["]}`.
- Make it the single push-on-by-default key without changing any other category's default:
  add `_PUSH_ON_BY_DEFAULT = {"instance_crash"}` and have `default_routing()` set
  `"push": t["key"] in _PUSH_ON_BY_DEFAULT`. All existing categories keep `push=False`
  default (regression-tested).
- Web/mobile settings screens are data-driven and pick it up automatically; also add the
  entry to the web `FALLBACK_TYPES` (`NotificationSettingsView.vue`) and mobile
  `kNotificationCategories` (`notification_prefs.dart`) so it shows before a fresh fetch.

### 2. `alert_instance_crash()` (`backend/live_alerts.py`)
- Mirror `alert_crash_loop`. Signature roughly:
  `alert_instance_crash(*, instance_id: str, reason: str, detail: str = "") -> None`.
- Builds content `INSTANCE CRASH [<id>] <reason>` + an embed carrying the reason and a
  truncated traceback; concise push title/body (e.g. title `Instance crashed: <id>`).
- Calls `notify(category="instance_crash", instance_id=..., discord_channel="notifications",
  ...)`. Best-effort, never raises.

### 3. Crash-keepalive guard (`backend/instance.py`) — core

Module state:
- `_KEEPALIVE_ENABLED: bool` — set once at startup from the Instances row: `kind != 'kalshi'`.
- `_crash_lock = threading.Lock()`, `_crash_entered = False` — idempotency.
- Reuse existing `CRASH_LOOP_*` constants; add `CHANGEFEED_RETRY_MAX` (default 3) and a small
  backoff.

`enter_crash_keepalive(reason: str, exc: BaseException | None = None) -> None`:
1. Idempotent under `_crash_lock` (first caller wins; later callers return).
2. Terminate the broker subprocess if alive (halt trading).
3. Write a crash banner + full `traceback.format_exc()`/`exc` to **both** the console (via
   `intellistock_logger`) and the live-trading log **file** `instance_<id>.log` (append, via
   the `live_state` path helper) so the crash reason shows in logs-history.
4. Best-effort update the Instances row: `crashed=True, crashed_at=r.now(), running=True`.
5. Best-effort `alert_instance_crash(instance_id, reason, detail=<short traceback>)`.
6. Block forever: loop `sleep(2)`; on each tick best-effort read `runCommand`; exit
   `os._exit(0)` only when it is `False` (operator Stop). DB-read failure → keep blocking.

Coordination: daemon threads that detect a fatal condition call `enter_crash_keepalive`
(idempotent: it terminates the broker, notifies, marks the row) **and** set a shared
`_crash_entered` flag; the **main** supervisor loop checks the flag and itself enters the
indefinite block, so the broker stays terminated process-wide rather than being restarted.

Wiring (all new branches gated by `_KEEPALIVE_ENABLED`; Kalshi falls through to today's code):
- `run()` body + `__main__` call wrapped in `try/except BaseException` → `enter_crash_keepalive`.
- Supervisor loop (`:383-393`): wrap so any exception → `enter_crash_keepalive`; also break to
  the block if `_crash_entered` is set; do not restart the broker once crashed.
- `terminate_thread_on_command` (`:89-92`) and `run_instance_stocks_changefeed` (`:256-259`):
  on changefeed error, **retry the changefeed up to `CHANGEFEED_RETRY_MAX` times with short
  backoff**; if still failing → equities: `enter_crash_keepalive("<feed> changefeed lost")`;
  Kalshi: existing `os._exit(0)`. The `runCommand=False` branch (`:82-88`) is unchanged.
- Crash-loop latch (`:142-164`): after the existing `alert_crash_loop`, equities →
  `enter_crash_keepalive("broker crash-loop latched")` instead of returning to the spin loop.
- Startup DB-connect failure (`:296-299`): equities → best-effort `enter_crash_keepalive`
  (notify is best-effort since the DB is down); Kalshi → existing `os._exit(1)`.

### 4. Dashboard "crashed" badge (web + mobile)
- Backend already exposes the Instances row in the instances/overview payloads; surface
  `crashed` / `crashed_at` where instance run-status is rendered and show a "CRASHED" badge.
- Clearing: `crashed`/`crashed_at` are reset on the next successful Start (where `running`/
  `uptimeStart` are already set in `run()` startup).

## Recovery (unchanged for the operator)
Operator presses Stop → `runCommand=False`. The keep-alive poll sees it and exits; and as a
backstop the server's Instances changefeed still calls `stop_instance_container()`
(`container.stop()`), so even a fully wedged instance is torn down. Start then recreates a
fresh container and clears the crashed flags.

## Honest limit on "no docker should kill the container"
The block defeats every **application-level** death (exceptions, `os._exit`) — the actual
problem. It does **not** trap OS signals: the operator Stop path relies on the server's
`container.stop()` (SIGTERM) to recover the instance, so trapping SIGTERM would break Stop.
An explicit `docker-compose down` / `docker kill` (a deliberate teardown, not a crash) will
still SIGKILL after the stop grace period — and there the logs are already flushed to the
shared volume, so nothing is lost. Net: nothing automatic or accidental stops the container
anymore; only an explicit teardown or the Stop button can.

## Testing
- `notification_types`: `instance_crash` present; `default_routing()['instance_crash'].push
  is True`; **regression** — every other category still defaults `push=False`.
- `live_alerts.alert_instance_crash`: enqueues with category `instance_crash` and the
  `INSTANCE CRASH [` prefix; never raises.
- `instance.py`: factor the exit decisions into testable units. Cover — `enter_crash_keepalive`
  idempotency; equities changefeed error after retries → keepalive (not `os._exit`); Kalshi
  changefeed error → `os._exit(0)` (today's behavior); `runCommand=False` still exits;
  crash-loop latch → keepalive when equities. Mock `os._exit`, the DB, and the broker process.
- Full backend Kalshi/notification suites stay green; `flutter analyze` clean; web `vite build`
  green. Run `gitnexus_impact` on each edited symbol before editing and
  `gitnexus_detect_changes` before commit (per CLAUDE.md).

## Out of scope
- Changefeed auto-reconnect beyond the bounded retry (the operator chose retry-then-halt).
- Auto-restarting a crashed instance (operator restarts manually so logs are preserved).
- Kalshi crash keep-alive (Kalshi keeps `runner.py` behavior; can be added later).
