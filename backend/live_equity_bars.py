"""Live-mode daily bars for equity run_once strategies.

WHY: the live main loop passes ``data=None`` to ``run_run_once_strategies`` —
only BACKTEST builds a bars history. Strategy X, XS and EB all read daily closes
from ``data``, so live they see nothing and correctly REFUSE to trade. This
module builds the per-tick ``data`` dict for equity instances from a
broker-injected fetch function.

Import-safe on purpose (no broker import): broker.py is a script with side
effects (argparse at module scope SystemExits under pytest), so the pure logic
lives here where tests can reach it — same pattern as live_crypto_bars.

Safety contract:
- Returns {symbol: [bar, ...]} on success (a symbol may be empty when there is
  nothing yet — the strategy refuses on its own and that is the correct answer).
- A symbol whose fetch came back empty, OLDER than the snapshot already held, or
  materially shorter than it (see ``is_degraded``) is served from ``last_good``:
  stale bars beat blinding, or half-blinding, a strategy holding a 3x fund. A
  fetch that is merely a few bars shorter is normal calendar drift and is
  SERVED — treating it as staleness ratchets the snapshot and blinds the
  strategy for weeks at a time.
- Returns ``None`` ONLY when the fetch failed outright and there is no last-good
  snapshot. The caller must then SKIP the tick's run_once strategies.
"""

from __future__ import annotations

import datetime
from typing import Callable, Iterable, Mapping, Optional

#: 400 calendar days ~= 275 trading sessions: more than the 60-bar slow vol
#: window and the 200-bar filters other strategies on this path use, with room
#: for holidays. Cheap — one 1Day request per symbol per tick.
LOOKBACK_DAYS_DEFAULT = 400


#: `strategy_eb`'s two accepted spellings, matching
#: `broker._strategy_eb_universe_symbols`.
EB_STRATEGY_NAMES = frozenset({"strategy_eb", "strategyeb"})

#: Lanes allowed beside strategy_eb on one document. The guard below exists
#: because graph_nexus_analysis reads `data is not None` as "backtest"; the
#: outlier sleeve reads no bars at all (it reads OutlierUniverseFeatures), so
#: handing bars to a document that carries it changes nothing for it.
COMPANION_LANES = frozenset({"outlier_sleeve", "outliersleeve"})


def other_enabled_run_once_lanes(run_once_specs) -> list:
    """Names of run_once lanes on this document that are NOT strategy_eb.

    Design spec §8 says the EB document must carry no other enabled lane. That
    is a LIVE CORRECTNESS rule, not a preference, which is why it is enforced in
    code: `graph_nexus_analysis` discriminates live from backtest on
    ``data is not None`` (`_bz_set_bt_main(data is not None)`), so the moment
    this module hands real bars to a document that also runs GNA, GNA flips into
    BACKTEST budget mode on a live tick. Any non-empty result means the caller
    must leave ``data=None``.

    A lane counts as enabled unless its own config explicitly says otherwise
    (``<name>_enabled`` or ``enabled`` present and falsy). Absent means enabled:
    most run_once strategies — GNA among them — have no enable key at all, and
    the failure this guards is silent, so the guard fails CLOSED.
    """
    out = []
    for spec in (run_once_specs or []):
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("strategy") or "").strip().lower()
        if not name or name in EB_STRATEGY_NAMES or name in COMPANION_LANES:
            continue
        # `run_run_once_strategies` skips a zero-weight spec outright
        # (broker.py:6923), and `_strategy_x_specs` (broker.py:4311) mirrors
        # that test for exactly this reason. A lane that will not be CALLED
        # cannot read `data`, so it cannot be the lane that blocks EB. Note
        # the default: a spec with no weight at all is skipped, because the
        # broker's `spec.get("weight", 0)` skips it too.
        try:
            if float(spec.get("weight", 0) or 0) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        # Settings are `conditions` under `config`, the same merge
        # `run_run_once_strategies` performs (broker.py:6931-6937) before it
        # hands them to the strategy. Reading `config` alone would miss a lane
        # switched off in `conditions`.
        merged = {}
        for layer in (spec.get("conditions"), spec.get("config")):
            if isinstance(layer, Mapping):
                merged.update(layer)
        disabled = False
        for key in (f"{name}_enabled", "enabled"):
            if key in merged and not _truthy(merged.get(key)):
                disabled = True
                break
        if not disabled and name not in out:
            out.append(name)
    return out


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def lookback_start(now_utc: datetime.datetime,
                   lookback_days: int = LOOKBACK_DAYS_DEFAULT
                   ) -> datetime.datetime:
    """Fetch-window start: ``lookback_days`` calendar days before ``now_utc``."""
    return now_utc - datetime.timedelta(days=max(1, int(lookback_days)))


#: A fetch shorter than this fraction of the series already held is a partial
#: outage, not calendar drift. `fetch_alpaca_historical_bars` stitches its
#: window from chunks and SWALLOWS a failed chunk, so a partial outage returns
#: ~24 closes where ~275 are expected — 8%, far under the floor — while the
#: honest wobble is 272-276 sessions per 400 calendar days depending on which
#: NYSE holidays land inside the window (~1.5%). Anything in between is treated
#: as real data.
MIN_LENGTH_RATIO = 0.9

#: The keys a bar's timestamp arrives under, in the order
#: `strategy_x.pit_daily_observations` reads them.
_STAMP_KEYS = ("t", "timestamp", "date")


def _as_dt(value) -> Optional[datetime.datetime]:
    """Best-effort UTC datetime from a bar stamp; None when unparseable."""
    if isinstance(value, datetime.datetime):
        stamp = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            stamp = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return stamp.astimezone(datetime.timezone.utc)


def newest_stamp(bars) -> Optional[datetime.datetime]:
    """The latest parseable timestamp in a bar series, or None."""
    newest = None
    for bar in (bars or []):
        if not isinstance(bar, Mapping):
            continue
        for key in _STAMP_KEYS:
            if key in bar:
                stamp = _as_dt(bar.get(key))
                if stamp is not None and (newest is None or stamp > newest):
                    newest = stamp
                break
    return newest


def is_degraded(bars, held) -> bool:
    """Is ``bars`` a worse view of the tape than the ``held`` snapshot?

    Only two things make it worse, and neither is "a few bars shorter":

    (a) It is OLDER — its newest bar predates the newest one already held. A
        feed serving a stale window is the case that freezes `session_id` and
        the missing-price fallback.
    (b) It is MATERIALLY shorter — under `MIN_LENGTH_RATIO` of what is held,
        which is a swallowed chunk, not a calendar.

    The naive `len(bars) < len(held)` test that this replaces ratcheted: the
    400-calendar-day window holds 272-276 sessions depending on which NYSE
    holidays fall inside it, and because the served output is re-stored as the
    next `last_good`, its length became a running MAXIMUM. Simulated over
    2025-06 -> 2026-06 that served a frozen snapshot on 141 of 250 trading
    days, in runs of 52, 39, 25 and 13 — the exact blinding this function
    exists to prevent, caused by the guard against it.
    """
    if not held:
        return False
    if not bars:
        return True
    newest_new, newest_held = newest_stamp(bars), newest_stamp(held)
    if newest_new is not None and newest_held is not None:
        # A fetch at least as NEW is real data even when it carries a few
        # fewer bars; only a materially short one is an outage.
        return (newest_new < newest_held
                or len(bars) < MIN_LENGTH_RATIO * len(held))
    # Unparseable stamps on either side: length is all that is left.
    return len(bars) < MIN_LENGTH_RATIO * len(held)


#: How many NYSE sessions a snapshot may lag before it stops counting as data.
#: `is_degraded` only ever compares a fetch against the snapshot already held,
#: which a FROZEN feed passes on every tick — same newest bar, same length — so
#: without an ABSOLUTE bound a stale window is served forever and the strategy
#: keeps sizing a levered position off a volatility it measured weeks ago.
#: Three leaves two sessions of slack over the normal one-session lag (the
#: daily bar for the session in progress does not exist until its close), which
#: covers every NYSE holiday run in the calendar.
MAX_STALE_SESSIONS_DEFAULT = 3

#: Beyond this the session count is not worth walking day by day; anything that
#: far back is stale under any bound anyone would configure.
_STALE_WALK_LIMIT_DAYS = 45


def sessions_since(stamp: datetime.datetime,
                   now_utc: datetime.datetime) -> int:
    """NYSE sessions strictly after ``stamp``'s date, through ``now_utc``'s.

    Weekdays, not the exchange calendar: this module is deliberately import-pure
    (no broker import, no `exchange_calendars` dependency) so its logic stays
    reachable from tests. Counting a holiday as a session can only make the
    bound TIGHTER, and the default carries two sessions of slack for exactly
    that.
    """
    start = stamp.astimezone(datetime.timezone.utc).date()
    end = now_utc.astimezone(datetime.timezone.utc).date()
    if end <= start:
        return 0
    if (end - start).days > _STALE_WALK_LIMIT_DAYS:
        return _STALE_WALK_LIMIT_DAYS
    count, day = 0, start + datetime.timedelta(days=1)
    while day <= end:
        if day.weekday() < 5:
            count += 1
        day += datetime.timedelta(days=1)
    return count


def build_live_equity_data(
    fetch_bars: Callable[[list, datetime.datetime, datetime.datetime],
                         Optional[Mapping]],
    symbols: Iterable[str],
    now_utc: datetime.datetime,
    lookback_days: int = LOOKBACK_DAYS_DEFAULT,
    last_good: Optional[Mapping] = None,
    log: Optional[Callable[[str, str], None]] = None,
    required: Optional[Iterable[str]] = None,
    max_stale_sessions: int = MAX_STALE_SESSIONS_DEFAULT,
) -> Optional[dict]:
    """Assemble the live ``data`` dict for equity run_once strategies.

    ``required`` names the symbols this tick cannot run blind on — the
    reference index whose closes drive the whole transform, and anything the
    book is currently holding. If one of them comes back empty and there is no
    last-good to cover it, the whole snapshot is refused rather than handed
    over as a partial success.

    ``max_stale_sessions`` bounds how far the newest bar in the result may lag
    ``now_utc``. 0 disables the bound.
    """

    def _log(message, color="yellow"):
        if log is not None:
            try:
                log(message, color)
            except Exception:
                pass

    syms, seen = [], set()
    for raw in (symbols or []):
        upper = str(raw or "").strip().upper()
        if upper and upper not in seen:
            seen.add(upper)
            syms.append(upper)
    if not syms:
        return {}
    syms.sort()

    start = lookback_start(now_utc, lookback_days)
    try:
        fetched = fetch_bars(syms, start, now_utc)
    except Exception as exc:
        _log(f"Live equity bars fetch raised: {type(exc).__name__}: {exc}",
             "red")
        fetched = None

    need = []
    for raw in (required or []):
        upper = str(raw or "").strip().upper()
        if upper and upper not in need:
            need.append(upper)

    def _fresh_enough(snapshot):
        """None when the snapshot is too old to be data. C1: `is_degraded` only
        compares a fetch against what is already held, so a feed that answers
        every tick with the SAME stale window satisfies every other guard here
        and is served forever."""
        if int(max_stale_sessions or 0) <= 0:
            return snapshot
        newest = None
        for bars in (snapshot or {}).values():
            stamp = newest_stamp(bars)
            if stamp is not None and (newest is None or stamp > newest):
                newest = stamp
        if newest is None:
            # Unmeasurable, not proven stale. Blinding the strategy on a
            # stamp-format change would be the worse failure of the two, so
            # this serves — loudly.
            _log("Live equity bars: staleness UNMEASURABLE — no parseable "
                 "timestamp anywhere in the snapshot. Serving it, but the "
                 "freshness guard is inert until the stamp format is fixed.",
                 "red")
            return snapshot
        lag = sessions_since(newest, now_utc)
        if lag > int(max_stale_sessions):
            _log(f"Live equity bars: snapshot is STALE — newest bar "
                 f"{newest.date()} is {lag} sessions behind "
                 f"{now_utc.date()} (bound {max_stale_sessions}). Skipping "
                 "strategies this tick: a levered position sized off a "
                 "volatility measured weeks ago is worse than no position "
                 "decision at all.", "red")
            return None
        return snapshot

    if isinstance(fetched, Mapping) and any(fetched.get(s) for s in syms):
        out, stale, truncated = {}, [], []
        for symbol in syms:
            bars = list(fetched.get(symbol) or [])
            held = list((last_good or {}).get(symbol) or [])
            if held and is_degraded(bars, held):
                (stale if not bars else truncated).append(symbol)
                bars = held
            out[symbol] = bars
        # C2: a partial fetch was treated as success as long as ANY symbol came
        # back. With no last-good behind it, the reference index could be empty
        # and the tick ran anyway — and a strategy that cannot see its own
        # reference closes cannot evaluate its own risk.
        blind = [symbol for symbol in need if not out.get(symbol)]
        if blind:
            _log("Live equity bars: PARTIAL fetch — no bars at all for "
                 + ", ".join(blind)
                 + ", and no last-good snapshot to cover them. Skipping "
                   "strategies this tick rather than running one blind to the "
                   "series it needs.", "red")
            return None
        if stale:
            _log("Live equity bars: empty fetch for " + ", ".join(stale)
                 + " — reusing last-good bars (stale) rather than blinding a "
                   "strategy that is holding a levered position.", "yellow")
        if truncated:
            _log("Live equity bars: DEGRADED fetch for " + ", ".join(truncated)
                 + " — older or materially shorter than the series already "
                   "held; reusing the last-good one. A short window "
                   "under-measures volatility, and under-measured risk sizes a "
                   "levered position LARGER.", "yellow")
        return _fresh_enough(out)

    if last_good:
        _log("Live equity bars fetch FAILED — reusing the last-good snapshot "
             "(stale) for this tick.", "red")
        return _fresh_enough(dict(last_good))
    _log("Live equity bars fetch FAILED with no last-good snapshot — caller "
         "must skip strategies this tick.", "red")
    return None
