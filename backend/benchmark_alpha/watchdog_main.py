"""Alpha mark/equity watchdog subprocess entrypoint (Task 6 Step 9).

Runs OUT-OF-PROCESS from the broker. Disabled by default: `instance.py`
launches it only when ``ALPHA_MARK_WATCHDOG_ENABLED=1``, and this entrypoint
refuses to start without its own scoped credentials
(``ALPACA_WATCHDOG_KEY``/``ALPACA_WATCHDOG_SECRET``) and a reachable
Postgres — the deployment prerequisites named by the plan. If Alpaca cannot
issue a separately scoped credential for the account, the operator may set
these to the shared runtime credential, accepting the residual risk recorded
in the LIVE_40 sign-off (Task 6 Step 8a).

Self-healing (2026-10-02). On 2026-10-01 at 05:48:52 UTC, during a DNS
outage, this process stopped writing control health for swing-paper and
alpaca-main while staying alive, and the order gate refused every opening
order for 37 hours. alpaca-py sends its requests with no timeout, so one
Alpaca call on a half-open connection could block a poll forever; the loop
caught exceptions but nothing bounded a poll that never returned. Now:

- every Alpaca request carries a default timeout (``WATCHDOG_HTTP_TIMEOUT_SEC``,
  15 s), as the broker adapter's already does;
- each poll runs under a deadline (``WATCHDOG_POLL_DEADLINE_SEC``, 120 s). A
  poll that overruns it is recorded as a failure (itself time-bounded) and the
  process exits with code 3, abandoning the wedged sockets; the instance
  supervisor restarts a watchdog that exited or went silent;
- nothing a single iteration raises, including a write to a closed stderr,
  ends the loop.
"""
import argparse
import os
import sys
import threading
import time
from datetime import datetime, timezone

#: The exit code of a poll that overran its deadline.
EXIT_WEDGED = 3

DEFAULT_HTTP_TIMEOUT_SEC = 15.0
DEFAULT_POLL_DEADLINE_SEC = 120.0
DEFAULT_FAILURE_RECORD_TIMEOUT_SEC = 20.0


class PollDeadlineExceeded(RuntimeError):
    """A poll did not return within its deadline."""


def _env_seconds(name, default):
    try:
        value = float(os.environ.get(name, "") or default)
    except ValueError:
        return float(default)
    return value if value > 0 else float(default)


def _bound_http_session(client, timeout):
    """Give every request of an alpaca-py client a default timeout.

    alpaca-py calls ``requests.Session.request`` with no timeout. A caller's
    own timeout (keyword or positional) is kept. Idempotent."""
    session = getattr(client, "_session", None)
    if session is None or getattr(session, "_intellistock_timeout_patched", False):
        return client
    original = session.request

    def request_with_timeout(method, url, *args, **kwargs):
        if not args and kwargs.get("timeout") is None:
            kwargs["timeout"] = timeout
        return original(method, url, *args, **kwargs)

    session.request = request_with_timeout
    session._intellistock_timeout_patched = True
    return client


def _build_runtime(instance_id):
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    from db import store as db_store
    from benchmark_alpha.pg_store import (
        AlphaPostgresStore,
        AlphaStateConflictError,
    )
    from benchmark_alpha.watchdog import AlphaWatchdog

    key = os.environ["ALPACA_WATCHDOG_KEY"]
    secret = os.environ["ALPACA_WATCHDOG_SECRET"]
    paper = os.environ.get("ALPACA_WATCHDOG_PAPER", "0") == "1"
    client = TradingClient(api_key=key, secret_key=secret, paper=paper)
    _bound_http_session(
        client, _env_seconds("WATCHDOG_HTTP_TIMEOUT_SEC", DEFAULT_HTTP_TIMEOUT_SEC))

    store = AlphaPostgresStore()

    class Probe:
        def broker_equity(self):
            return float(client.get_account().equity or 0.0)

        def broker_positions(self):
            out = {}
            for p in client.get_all_positions():
                try:
                    out[str(p.symbol)] = float(p.qty)
                except (TypeError, ValueError):
                    continue
            return out

        def cancel_entry_orders(self):
            req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
            for order in client.get_orders(filter=req):
                if str(getattr(order, "side", "")).lower().endswith("buy"):
                    try:
                        client.cancel_order_by_id(order.id)
                    except Exception:
                        continue

        def halt_instance(self):
            db_store.update("Instances", instance_id, {"runCommand": False})

    def write_health(evidence):
        key = f"control_health:{instance_id}"
        for _attempt in range(3):
            current = store.get_state(key)
            expected = current.version if current is not None else 0
            try:
                store.put_state(key, evidence.to_doc(), expected)
                return
            except AlphaStateConflictError:
                continue
        raise AlphaStateConflictError(
            "watchdog control-health CAS retries exhausted"
        )

    watchdog = AlphaWatchdog(
        probe=Probe(), rethink_store=store, thresholds={},
        instance_id=instance_id,
        reduce_executor=None,
        health_writer=write_health,
    )
    return watchdog


def _say(stream, message):
    """Print without ever raising: a closed stderr must not end the loop."""
    try:
        print(message, file=stream if stream is not None else sys.stdout,
              flush=True)
    except Exception:
        pass


def _bounded(fn, timeout):
    """Run ``fn`` on a daemon thread for at most ``timeout`` seconds.

    Returns ``("ok", value)``, ``("error", exc)`` or ``("timeout", None)``. A
    timed-out thread is abandoned; the caller exits the process."""
    outcome = {}

    def target():
        try:
            outcome["value"] = fn()
        except BaseException as exc:  # reported to the caller, never re-raised here
            outcome["error"] = exc

    worker = threading.Thread(target=target, name="watchdog-poll", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        return "timeout", None
    if "error" in outcome:
        return "error", outcome["error"]
    return "ok", outcome.get("value")


def _now():
    return datetime.now(timezone.utc)


def run_loop(watchdog, *, poll_seconds, poll_deadline, sleep=time.sleep,
             exit_process=os._exit, clock=_now, out=None, err=None,
             max_polls=None,
             failure_record_timeout=DEFAULT_FAILURE_RECORD_TIMEOUT_SEC):
    """Poll forever (or ``max_polls`` times, for tests).

    A poll that raises is recorded with ``record_failure`` and the loop goes
    on; the next successful poll writes healthy evidence again. A poll that
    overruns ``poll_deadline`` is recorded and ``exit_process(3)`` is called,
    so the supervisor starts a fresh process."""
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    polls = 0
    while max_polls is None or polls < max_polls:
        polls += 1
        try:
            state, value = _bounded(lambda: watchdog.poll_once(clock()),
                                    poll_deadline)
            if state == "timeout":
                exc = PollDeadlineExceeded(
                    f"poll did not finish within {poll_deadline:.0f}s")
                rec_state, rec_value = _bounded(
                    lambda: watchdog.record_failure(clock(), exc),
                    failure_record_timeout)
                if rec_state != "ok":
                    _say(err, "[watchdog] could not record the wedged poll: "
                              f"{rec_state} {rec_value!r}")
                _say(err, f"[watchdog] {exc}; exiting (code {EXIT_WEDGED}) so "
                          "the instance supervisor restarts it")
                exit_process(EXIT_WEDGED)
                return polls
            if state == "error":
                exc = value
                if not isinstance(exc, Exception):
                    raise exc
                rec_state, rec_value = _bounded(
                    lambda: watchdog.record_failure(clock(), exc),
                    failure_record_timeout)
                if rec_state == "timeout":
                    _say(err, "[watchdog] recording a failed poll wedged; "
                              f"exiting (code {EXIT_WEDGED}) so the instance "
                              "supervisor restarts it")
                    exit_process(EXIT_WEDGED)
                    return polls
                _say(err, f"[watchdog] poll error: {type(exc).__name__}: {exc}")
            elif getattr(value, "status", "OK") != "OK":
                _say(out, f"[watchdog] {value.status} mismatches="
                          f"{len(value.mismatches)} "
                          f"degraded_audit={value.degraded_audit}")
        except Exception as exc:
            _say(err, f"[watchdog] loop error: {type(exc).__name__}: {exc}")
        try:
            sleep(poll_seconds)
        except Exception:
            pass
    return polls


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--poll-seconds", type=float,
                        default=float(os.environ.get("WATCHDOG_POLL_SEC", "30")))
    args = parser.parse_args(argv)

    missing = [name for name in ("ALPACA_WATCHDOG_KEY", "ALPACA_WATCHDOG_SECRET")
               if not os.environ.get(name)]
    if not (os.environ.get("PG_DSN") or os.environ.get("PGHOST")):
        missing.append("PG_DSN")
    if missing:
        print(f"[watchdog] refusing to start: missing env {missing} "
              "(scoped watchdog credentials are a deployment prerequisite)",
              file=sys.stderr)
        return 2

    watchdog = _build_runtime(args.instance_id)
    deadline = _env_seconds("WATCHDOG_POLL_DEADLINE_SEC", DEFAULT_POLL_DEADLINE_SEC)
    print(f"[watchdog] started for {args.instance_id} "
          f"(poll every {args.poll_seconds:.0f}s, deadline {deadline:.0f}s)")
    run_loop(watchdog, poll_seconds=args.poll_seconds, poll_deadline=deadline,
             exit_process=os._exit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
