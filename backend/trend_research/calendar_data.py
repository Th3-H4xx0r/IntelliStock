"""Static scheduled-event calendar for TR prompts (spec §7.1, plan Task 3).

FOMC decision days and the BLS CPI and Employment Situation ("jobs") release
days, 2020-2026. These dates are published well ahead, so each event is
available 30 days before it happens -- context only, never a signal of the
outcome.

Sources, read 2026-11-05: federalreserve.gov ``fomccalendars.htm`` (2021-2026;
2020 from the Fed's 2020 calendar), and the BLS release archives
``bls.gov/bls/news-release/cpi.htm`` / ``empsit.htm`` plus the 2026 schedules.

Unscheduled FOMC actions (2020-03-03, 2020-03-15, the 2025-08-22 notation
vote) are left out: none was on a calendar 30 days ahead.

Cancellations (``CANCELLED``): an event that was on the calendar and then did
not happen on that date -- the 2020-03-18 FOMC meeting, replaced by the
2020-03-15 emergency cut, and the original dates of the Sep-Nov 2025 CPI and
jobs releases the 2025 appropriations lapse moved or cancelled -- stays in the
table as scheduled, available 30 days ahead like any other, with
``cancelled: true`` and ``cancelled_available_at``, the time the cancellation
was public. Read with a ``decision_time``, a cancellation shows only once
``cancelled_available_at < decision_time``; before that the event reads as
scheduled. Where the announcement time is not on record, the cancellation is
stamped the day before the original date (00:00 UTC) -- later than the BLS
notices, never earlier.

Releases moved by the 2025-26 appropriations lapses were not known 30 days
ahead, so their new dates are stamped available only the day before
(``RESCHEDULED``).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

__all__ = ["event_calendar", "FOMC_DECISIONS", "CPI_RELEASES", "JOBS_RELEASES",
           "CANCELLED", "RESCHEDULED", "LEAD_DAYS"]

LEAD_DAYS = 30

# Last day of each scheduled meeting (statement at 14:00 ET).
FOMC_DECISIONS = (
    "2020-01-29", "2020-04-29", "2020-06-10", "2020-07-29", "2020-09-16",
    "2020-11-05", "2020-12-16",
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28",
    "2021-09-22", "2021-11-03", "2021-12-15",
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27",
    "2022-09-21", "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26",
    "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31",
    "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30",
    "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29",
    "2026-09-16", "2026-10-28", "2026-12-09",
)

# (release day, reference month) -- 08:30 ET.
CPI_RELEASES = (
    ("2020-02-13", "2020-01"), ("2020-03-11", "2020-02"), ("2020-04-10", "2020-03"),
    ("2020-05-12", "2020-04"), ("2020-06-10", "2020-05"), ("2020-07-14", "2020-06"),
    ("2020-08-12", "2020-07"), ("2020-09-11", "2020-08"), ("2020-10-13", "2020-09"),
    ("2020-11-12", "2020-10"), ("2020-12-10", "2020-11"), ("2021-01-13", "2020-12"),
    ("2021-02-10", "2021-01"), ("2021-03-10", "2021-02"), ("2021-04-13", "2021-03"),
    ("2021-05-12", "2021-04"), ("2021-06-10", "2021-05"), ("2021-07-13", "2021-06"),
    ("2021-08-11", "2021-07"), ("2021-09-14", "2021-08"), ("2021-10-13", "2021-09"),
    ("2021-11-10", "2021-10"), ("2021-12-10", "2021-11"), ("2022-01-12", "2021-12"),
    ("2022-02-10", "2022-01"), ("2022-03-10", "2022-02"), ("2022-04-12", "2022-03"),
    ("2022-05-11", "2022-04"), ("2022-06-10", "2022-05"), ("2022-07-13", "2022-06"),
    ("2022-08-10", "2022-07"), ("2022-09-13", "2022-08"), ("2022-10-13", "2022-09"),
    ("2022-11-10", "2022-10"), ("2022-12-13", "2022-11"), ("2023-01-12", "2022-12"),
    ("2023-02-14", "2023-01"), ("2023-03-14", "2023-02"), ("2023-04-12", "2023-03"),
    ("2023-05-10", "2023-04"), ("2023-06-13", "2023-05"), ("2023-07-12", "2023-06"),
    ("2023-08-10", "2023-07"), ("2023-09-13", "2023-08"), ("2023-10-12", "2023-09"),
    ("2023-11-14", "2023-10"), ("2023-12-12", "2023-11"), ("2024-01-11", "2023-12"),
    ("2024-02-13", "2024-01"), ("2024-03-12", "2024-02"), ("2024-04-10", "2024-03"),
    ("2024-05-15", "2024-04"), ("2024-06-12", "2024-05"), ("2024-07-11", "2024-06"),
    ("2024-08-14", "2024-07"), ("2024-09-11", "2024-08"), ("2024-10-10", "2024-09"),
    ("2024-11-13", "2024-10"), ("2024-12-11", "2024-11"), ("2025-01-15", "2024-12"),
    ("2025-02-12", "2025-01"), ("2025-03-12", "2025-02"), ("2025-04-10", "2025-03"),
    ("2025-05-13", "2025-04"), ("2025-06-11", "2025-05"), ("2025-07-15", "2025-06"),
    ("2025-08-12", "2025-07"), ("2025-09-11", "2025-08"), ("2025-10-24", "2025-09"),
    ("2025-12-18", "2025-11"), ("2026-01-13", "2025-12"), ("2026-02-13", "2026-01"),
    ("2026-03-11", "2026-02"), ("2026-04-10", "2026-03"), ("2026-05-12", "2026-04"),
    ("2026-06-10", "2026-05"), ("2026-07-14", "2026-06"), ("2026-08-12", "2026-07"),
    ("2026-09-11", "2026-08"), ("2026-10-14", "2026-09"), ("2026-11-10", "2026-10"),
    ("2026-12-10", "2026-11"),
)

# (release day, reference month) -- 08:30 ET.
JOBS_RELEASES = (
    ("2020-02-07", "2020-01"), ("2020-03-06", "2020-02"), ("2020-04-03", "2020-03"),
    ("2020-05-08", "2020-04"), ("2020-06-05", "2020-05"), ("2020-07-02", "2020-06"),
    ("2020-08-07", "2020-07"), ("2020-09-04", "2020-08"), ("2020-10-02", "2020-09"),
    ("2020-11-06", "2020-10"), ("2020-12-04", "2020-11"), ("2021-01-08", "2020-12"),
    ("2021-02-05", "2021-01"), ("2021-03-05", "2021-02"), ("2021-04-02", "2021-03"),
    ("2021-05-07", "2021-04"), ("2021-06-04", "2021-05"), ("2021-07-02", "2021-06"),
    ("2021-08-06", "2021-07"), ("2021-09-03", "2021-08"), ("2021-10-08", "2021-09"),
    ("2021-11-05", "2021-10"), ("2021-12-03", "2021-11"), ("2022-01-07", "2021-12"),
    ("2022-02-04", "2022-01"), ("2022-03-04", "2022-02"), ("2022-04-01", "2022-03"),
    ("2022-05-06", "2022-04"), ("2022-06-03", "2022-05"), ("2022-07-08", "2022-06"),
    ("2022-08-05", "2022-07"), ("2022-09-02", "2022-08"), ("2022-10-07", "2022-09"),
    ("2022-11-04", "2022-10"), ("2022-12-02", "2022-11"), ("2023-01-06", "2022-12"),
    ("2023-02-03", "2023-01"), ("2023-03-10", "2023-02"), ("2023-04-07", "2023-03"),
    ("2023-05-05", "2023-04"), ("2023-06-02", "2023-05"), ("2023-07-07", "2023-06"),
    ("2023-08-04", "2023-07"), ("2023-09-01", "2023-08"), ("2023-10-06", "2023-09"),
    ("2023-11-03", "2023-10"), ("2023-12-08", "2023-11"), ("2024-01-05", "2023-12"),
    ("2024-02-02", "2024-01"), ("2024-03-08", "2024-02"), ("2024-04-05", "2024-03"),
    ("2024-05-03", "2024-04"), ("2024-06-07", "2024-05"), ("2024-07-05", "2024-06"),
    ("2024-08-02", "2024-07"), ("2024-09-06", "2024-08"), ("2024-10-04", "2024-09"),
    ("2024-11-01", "2024-10"), ("2024-12-06", "2024-11"), ("2025-01-10", "2024-12"),
    ("2025-02-07", "2025-01"), ("2025-03-07", "2025-02"), ("2025-04-04", "2025-03"),
    ("2025-05-02", "2025-04"), ("2025-06-06", "2025-05"), ("2025-07-03", "2025-06"),
    ("2025-08-01", "2025-07"), ("2025-09-05", "2025-08"), ("2025-11-20", "2025-09"),
    ("2025-12-16", "2025-11"), ("2026-01-09", "2025-12"), ("2026-02-11", "2026-01"),
    ("2026-03-06", "2026-02"), ("2026-04-03", "2026-03"), ("2026-05-08", "2026-04"),
    ("2026-06-05", "2026-05"), ("2026-07-02", "2026-06"), ("2026-08-07", "2026-07"),
    ("2026-09-04", "2026-08"), ("2026-10-02", "2026-09"), ("2026-11-06", "2026-10"),
    ("2026-12-04", "2026-11"),
)

# Releases moved at short notice by the 2025-26 appropriations lapses: the new
# date was not on any calendar 30 days ahead. (The two early-2026 entries are
# off their usual weekday and are treated the same way, conservatively.)
RESCHEDULED = frozenset({
    ("2025-10-24", "cpi"), ("2025-12-18", "cpi"), ("2026-02-13", "cpi"),
    ("2025-11-20", "jobs"), ("2025-12-16", "jobs"), ("2026-02-11", "jobs"),
})

# (original date, kind, reference month or None) -> when the cancellation was public.
CANCELLED = {
    ("2020-03-18", "fomc", None): "2020-03-15T21:00:00Z",   # Sunday 17:00 ET emergency cut
    ("2025-10-03", "jobs", "2025-09"): "2025-10-02T00:00:00Z",
    ("2025-10-15", "cpi", "2025-09"): "2025-10-14T00:00:00Z",
    ("2025-11-07", "jobs", "2025-10"): "2025-11-06T00:00:00Z",
    ("2025-11-13", "cpi", "2025-10"): "2025-11-12T00:00:00Z",
    ("2025-12-05", "jobs", "2025-11"): "2025-12-04T00:00:00Z",
    ("2025-12-10", "cpi", "2025-11"): "2025-12-09T00:00:00Z",
}

_LABELS = {"fomc": "FOMC rate decision (14:00 ET)",
           "cpi": "CPI release for %s (08:30 ET)",
           "jobs": "Employment Situation (jobs report) for %s (08:30 ET)"}


def _iso_midnight(d: date) -> str:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _event(day: str, kind: str, ref_month: str | None) -> dict:
    d = date.fromisoformat(day)
    lead = 1 if (day, kind) in RESCHEDULED else LEAD_DAYS
    ev = {"date": day, "kind": kind,
          "label": _LABELS[kind] % ref_month if ref_month else _LABELS[kind],
          "available_at": _iso_midnight(d - timedelta(days=lead))}
    if ref_month:
        ev["ref_month"] = ref_month
    return ev


def _all_events() -> list:
    events = [_event(d, "fomc", None) for d in FOMC_DECISIONS]
    events += [_event(d, "cpi", m) for d, m in CPI_RELEASES]
    events += [_event(d, "jobs", m) for d, m in JOBS_RELEASES]
    for (day, kind, ref_month), public_at in CANCELLED.items():
        ev = _event(day, kind, ref_month)
        ev["cancelled"] = True
        ev["cancelled_available_at"] = public_at
        events.append(ev)
    events.sort(key=lambda e: (e["date"], e["kind"]))
    return events


_EVENTS = tuple(_all_events())


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def event_calendar(start: date, end: date, decision_time: datetime | None = None) -> list:
    """Scheduled events dated ``start..end`` (inclusive), oldest first; each
    ``{date, kind, label, available_at[, ref_month][, cancelled,
    cancelled_available_at]}``.

    With ``decision_time`` (what a prompt must use): only events with
    ``available_at < decision_time``, and a cancellation only once
    ``cancelled_available_at < decision_time`` -- before that a cancelled
    event reads as scheduled. Without it, the raw table, cancellations
    included."""
    if not isinstance(start, date) or not isinstance(end, date):
        raise ValueError("start and end must be dates")
    if end < start:
        raise ValueError("end %s is before start %s" % (end, start))
    if decision_time is not None and (not isinstance(decision_time, datetime)
                                      or decision_time.utcoffset() is None):
        raise ValueError("decision_time must be a tz-aware datetime")
    lo, hi = start.isoformat(), end.isoformat()
    out = []
    for e in _EVENTS:
        if not lo <= e["date"] <= hi:
            continue
        ev = dict(e)
        if decision_time is not None:
            if not _parse_utc(ev["available_at"]) < decision_time:
                continue
            if ev.get("cancelled") and not (
                    _parse_utc(ev["cancelled_available_at"]) < decision_time):
                del ev["cancelled"], ev["cancelled_available_at"]
        out.append(ev)
    return out
