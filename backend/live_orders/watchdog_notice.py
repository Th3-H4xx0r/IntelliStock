"""What an order-gate refusal on the watchdog dependency means (2026-10-02).

The gate refuses new exposure on ``dependency.watchdog.*`` when the watchdog
sidecar's ``control_health`` evidence is unhealthy, missing, or older than its
60 s budget. On 2026-10-01 the sidecar went silent during a DNS outage, and
every swing approval was put back to pending with "approve again", which could
never succeed. This module owns two things the broker reports with:

- ``refusal_advice``: the operator-facing text. It names the health monitor
  and asks for an instance restart instead of another approval.
- ``WatchdogOutageLatch``: one alert per outage, not one per refusal or tick.

Pure: no I/O, no clock of its own. The broker supplies the evidence's
``observed_at`` and the current time.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

WATCHDOG_CODE_PREFIX = "dependency.watchdog."

#: The codes that mean the monitor is DOWN (silent or never reported), as
#: opposed to up and reporting an unhealthy account.
_DOWN_CODES = ("dependency.watchdog.stale", "dependency.watchdog.unknown")

#: A stale stamp younger than this is a gap the instance supervisor heals on
#: its own (it restarts a watchdog that is silent for 180 s), so it is not
#: worth a push.
DEFAULT_ALERT_AFTER = timedelta(
    seconds=float(os.environ.get("WATCHDOG_DOWN_ALERT_AFTER_SEC", "300") or 300))

_RESTART = "the instance needs a restart (Stop, then Start)"
_REFUSED = "approving before then is refused the same way"


def watchdog_codes(codes: Iterable[str]) -> tuple:
    """The ``dependency.watchdog.*`` codes among ``codes``, in order."""
    return tuple(str(code) for code in (codes or ())
                 if str(code).startswith(WATCHDOG_CODE_PREFIX))


def monitor_down(codes: Iterable[str]) -> bool:
    """True when the codes say the watchdog is silent or never reported."""
    return any(code in _DOWN_CODES for code in watchdog_codes(codes))


def _stamp(when: Optional[datetime]) -> str:
    if when is None:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def refusal_advice(codes: Iterable[str], last_report: Optional[datetime]) -> str:
    """Operator text for a refusal carrying watchdog codes."""
    if monitor_down(codes):
        since = (f"no report since {_stamp(last_report)}" if last_report
                 else "it has never reported")
        return (f"the instance's health monitor (watchdog) is down: {since}; "
                f"{_RESTART}, and {_REFUSED}")
    last = f" (last report {_stamp(last_report)})" if last_report else ""
    return (f"the instance's health monitor (watchdog) reports unhealthy{last}; "
            f"if that does not clear within a few minutes {_RESTART}, and "
            f"{_REFUSED}")


class WatchdogOutageLatch:
    """One alert per watchdog outage per instance.

    An outage is identified by the ``observed_at`` of the last evidence the
    watchdog wrote: while it is silent that stamp is frozen, so every refusal
    in the outage carries the same one. A watchdog that comes back and later
    goes silent again freezes at a new stamp, which is a new outage.

    Only a DOWN monitor alerts (``stale`` or ``unknown``), and only once its
    silence is older than ``alert_after``. A monitor that is up and reporting
    an unhealthy account is a genuine gate input, not an outage.
    """

    def __init__(self, *, alert_after: timedelta = DEFAULT_ALERT_AFTER):
        self._alert_after = alert_after
        self._alerted: dict = {}
        self._lock = threading.Lock()

    def should_alert(self, instance_id: str, codes: Iterable[str],
                     last_report: Optional[datetime], *, now: datetime) -> bool:
        if not monitor_down(codes):
            return False
        if last_report is not None:
            if last_report.tzinfo is None:
                last_report = last_report.replace(tzinfo=timezone.utc)
            if now - last_report < self._alert_after:
                return False
        key = str(instance_id)
        episode = last_report.isoformat() if last_report is not None else None
        with self._lock:
            if key in self._alerted and self._alerted[key] == episode:
                return False
            self._alerted[key] = episode
            return True
