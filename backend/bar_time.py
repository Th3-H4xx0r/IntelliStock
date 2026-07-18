"""Bar-timestamp parsing, extracted from broker.py so it's import-safe (no
argparse / DB / adapter side effects) and testable, and so the parse can be
sped up without touching broker.py's hot loop directly.

Alpaca bar times are ISO-8601 (e.g. "2026-07-12T13:45:00Z"). We parse with the
stdlib ``datetime.fromisoformat`` FIRST (~50x faster than ``dateutil``) and only
fall back to ``dateutil`` for anything non-ISO. Output is IDENTICAL to the prior
dateutil-first implementation: tz-aware inputs are converted to UTC and stripped
to naive; naive inputs are returned unchanged.
"""

from __future__ import annotations

import datetime


def bar_time_to_datetime(t_str):
    """Parse an Alpaca bar timestamp (ISO string or datetime) to naive UTC."""
    if t_str is None:
        return None
    if isinstance(t_str, datetime.datetime):
        dt = t_str
    else:
        dt = None
        try:
            s = t_str.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.datetime.fromisoformat(s)
        except Exception:
            try:
                from dateutil import parser as dateutil_parser
                dt = dateutil_parser.parse(t_str)
            except Exception:
                return None
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt
