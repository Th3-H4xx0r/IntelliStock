"""The TR research ledger: one row per chain, week, model and prompt version
(spec C1, C10; plan Task 7).

Key. ``id = chain_id|YYYY-MM-DD|model|prompt_version`` where the date is the
week's Monday. A chain is one forward-built sequence of weeks: a row's prior
state (trend list, recommended names, notes) comes only from the same
chain's previous ``ok`` row, so the honest chain starts cold (C10).

Time. ``as_of`` -- and ``available_at``, which equals it -- is 09:25 ET on
the week's first NYSE session: after the 09:00 ET digest window closes
(``ingest_gdelt.week_decision_time``) and before the 09:30 decision tick
that reads the row (``available_at < decision_time``).

Writes. ``put_week`` replaces a stored ``failed`` row (a later run retries
the week) but never an ``ok`` one: an ok row is what the lane replays, and a
silent rewrite would change a backtest after the fact. It does not go
through ``pit_store.put_rows``, whose keep-the-earliest rule would pin a
failed row forever (a retried week has the same ``available_at``).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from db.errors import StoreError
from trend_research.pit_store import parse_available_at, read_available

__all__ = ["TABLE", "STATUSES", "AS_OF_ET", "week_id", "week_as_of", "get_week",
           "latest_row", "put_week"]

TABLE = "TrendResearchLedger"
STATUSES = ("ok", "failed")
#: Wall-clock time (ET) of a week's ``as_of`` on its first session.
AS_OF_ET = (9, 25)
_ET = ZoneInfo("America/New_York")
_REQUIRED = ("id", "chain_id", "week_start", "as_of", "available_at", "model", "provider",
             "prompt_version", "status")


def _store(store):
    # The real store gets the TR tables ensured first (pit_store.ensure_tables).
    from trend_research import pit_store
    return pit_store._store(store)


def _part(name: str, value) -> str:
    if not isinstance(value, str) or not value.strip() or "|" in value:
        raise ValueError("%s must be a non-empty string without '|', got %r" % (name, value))
    return value.strip()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def week_id(chain_id: str, week_start: date, model: str, prompt_version: str) -> str:
    """``chain_id|YYYY-MM-DD|model|prompt_version`` (``week_start`` is the
    week's Monday)."""
    if not isinstance(week_start, date) or isinstance(week_start, datetime):
        raise ValueError("week_start must be a date, got %r" % (week_start,))
    if week_start.weekday() != 0:
        raise ValueError("week_start must be a Monday, got %s" % week_start)
    return "|".join((_part("chain_id", chain_id), week_start.isoformat(),
                     _part("model", model), _part("prompt_version", prompt_version)))


def week_as_of(monday: date) -> datetime:
    """09:25 ET on the first NYSE session of the week starting ``monday``
    (the Monday itself unless it is a holiday), as UTC."""
    from swing_trader.clock import trading_days
    sessions = sorted(trading_days(monday, monday + timedelta(days=4)))
    first = sessions[0] if sessions else monday
    return datetime.combine(first, time(*AS_OF_ET), tzinfo=_ET).astimezone(timezone.utc)


def get_week(chain_id: str, week_start: date, model: str, prompt_version: str, *,
             store=None) -> dict | None:
    """The stored row for that key, whatever its status, or None."""
    return _store(store).get(TABLE, week_id(chain_id, week_start, model, prompt_version))


def latest_row(chain_id: str, model: str, prompt_version: str, decision_time: datetime, *,
               store=None) -> dict | None:
    """The newest ``ok`` row of the chain for ``model`` / ``prompt_version``
    with ``as_of < decision_time`` (and ``available_at`` too), or None."""
    if (not isinstance(decision_time, datetime) or decision_time.utcoffset() is None):
        raise ValueError("decision_time must be a tz-aware datetime, got %r"
                         % (decision_time,))
    chain = _part("chain_id", chain_id)
    model = _part("model", model)
    prompt_version = _part("prompt_version", prompt_version)
    # Week keys sort by date; no week starting after the decision's day can
    # have an as_of before it.
    hi_day = (decision_time.astimezone(_ET).date() + timedelta(days=1)).isoformat()
    rows = read_available(TABLE, lo_id=chain + "|", hi_id=chain + "|" + hi_day,
                          decision_time=decision_time, store=_store(store))
    best, best_at = None, None
    for row in rows:
        if (row.get("chain_id") != chain or row.get("model") != model
                or row.get("prompt_version") != prompt_version or row.get("status") != "ok"):
            continue
        try:
            at = parse_available_at(row.get("as_of"), where="%s as_of" % row.get("id"))
        except ValueError:
            continue
        if at < decision_time and (best_at is None or at >= best_at):
            best, best_at = row, at
    return best


def put_week(row: dict, *, store=None) -> None:
    """Store one week's row (see the module docstring for the replace rule).

    ValueError when the row is malformed: a missing field, an id that is not
    ``week_id`` of its own fields, an unknown status, ``available_at`` not
    equal to ``as_of``, or an ``as_of`` that is not the week's
    ``week_as_of``; or when an ``ok`` row is already stored under the id.
    StoreError when the store rejects the write."""
    if not isinstance(row, dict):
        raise ValueError("ledger row must be a dict")
    missing = [k for k in _REQUIRED if row.get(k) in (None, "")]
    if missing:
        raise ValueError("ledger row missing %s" % ", ".join(missing))
    try:
        monday = date.fromisoformat(str(row["week_start"]))
    except ValueError:
        raise ValueError("week_start is not a date: %r" % (row["week_start"],)) from None
    expect = week_id(row["chain_id"], monday, row["model"], row["prompt_version"])
    if row["id"] != expect:
        raise ValueError("ledger id %r does not match its fields (%r)" % (row["id"], expect))
    if row["status"] not in STATUSES:
        raise ValueError("status must be one of %s, got %r" % (STATUSES, row["status"]))
    as_of = parse_available_at(row["as_of"], where="%s as_of" % expect)
    if parse_available_at(row["available_at"], where=expect) != as_of:
        raise ValueError("%s: available_at must equal as_of" % expect)
    if as_of != week_as_of(monday):
        raise ValueError("%s: as_of %s is not the week's %s"
                         % (expect, row["as_of"], _iso(week_as_of(monday))))
    st = _store(store)
    old = st.get(TABLE, expect)
    if isinstance(old, dict) and old.get("status") == "ok":
        raise ValueError("%s: an ok row is already stored; ok rows are never replaced" % expect)
    result = st.insert(TABLE, [dict(row)], conflict="replace")
    if result.errors:
        raise StoreError("%s %s: row rejected: %s" % (TABLE, expect, result.first_error))
