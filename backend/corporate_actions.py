"""Authoritative corporate-action lookup for split handling.

Price-ratio inference has a hard floor: a -90% collapse is arithmetically
identical to a 10:1 split. Across 345 one-bar discontinuities in this system's
caches only 96 were splits — 176 were genuine crashes, a 65% false-positive
rate. So inference can gate an order for a tick (cheap if wrong) but must never
silently restate prices (unbounded if wrong).

This module supplies the disambiguator. Benzinga's splits calendar is already
fetched elsewhere in the codebase and now emits a numeric `share_multiplier`
(`benzinga_client._fetch_splits_raw`); this wraps it in a process-level cache so
the bar loop can consult it without a network call per symbol per bar.

FAIL-OPEN by design. No token, no network, an API change — every failure path
returns None, and the caller falls back to inference-only behaviour, which is
what it did before this module existed. A corporate-actions lookup must never be
able to stop trading.
"""

import os
import threading
from datetime import datetime, timedelta

# ticker -> list of (date_str, share_multiplier), populated once per process.
_SPLIT_CACHE: dict[str, list[tuple[str, float]]] = {}
_CACHE_LOADED = False
_CACHE_WINDOW: tuple[str, str] | None = None
_LOCK = threading.Lock()


def _load(date_from: str, date_to: str) -> None:
    """Populate the process cache once. Silent and non-fatal on any failure."""
    global _CACHE_LOADED, _CACHE_WINDOW
    try:
        from benzinga_client import _fetch_splits_raw  # noqa: PLC0415

        token = (os.environ.get("BENZINGA_API_KEY")
                 or os.environ.get("BENZINGA_TOKEN") or "").strip()
        if not token:
            return
        for rec in _fetch_splits_raw(token, [], date_from, date_to, max_results=500) or []:
            mult = rec.get("share_multiplier")
            tic = str(rec.get("ticker") or "").strip().upper()
            date = str(rec.get("date") or "")[:10]
            if not tic or not date or mult is None:
                continue
            try:
                mult = float(mult)
            except (TypeError, ValueError):
                continue
            if mult <= 0 or mult == 1.0:
                continue
            _SPLIT_CACHE.setdefault(tic, []).append((date, mult))
        for v in _SPLIT_CACHE.values():
            v.sort()
    except Exception:
        # Fail open: the caller degrades to inference, exactly as before.
        pass
    finally:
        _CACHE_LOADED = True
        _CACHE_WINDOW = (date_from, date_to)


def split_multiplier_for(ticker, as_of=None, window_days: int = 5):
    """Authoritative share multiplier for `ticker` around `as_of`, or None.

    `as_of` is a date string or datetime; the lookup accepts an action dated
    within +/- `window_days`, because an unadjusted feed can show the step a bar
    or two off the official ex-date depending on when the venue applies it.

    Returns the SHARE multiplier — 8.0 for an 8-for-1, 0.1 for a 1-for-10 —
    matching `split_detect`'s contract (shares *= ratio, price /= ratio).
    """
    tic = str(ticker or "").strip().upper()
    if not tic:
        return None
    if isinstance(as_of, datetime):
        as_of = as_of.strftime("%Y-%m-%d")
    as_of = str(as_of or "")[:10]
    if not as_of:
        return None

    # The fetch is deliberately OUTSIDE the lock. _load reaches Benzinga through
    # _paginate (up to 5 pages x 30s timeout x 2 retries), so holding _LOCK
    # across it could stall every thread calling this for minutes — and the one
    # moment it can trigger is the first tick with a >=35% price step, i.e.
    # during a crash, exactly when the order loop must not block.
    if not _CACHE_LOADED:
        try:
            d = datetime.strptime(as_of, "%Y-%m-%d")
            _load((d - timedelta(days=400)).strftime("%Y-%m-%d"),
                  (d + timedelta(days=30)).strftime("%Y-%m-%d"))
        except ValueError:
            return None

    try:
        target = datetime.strptime(as_of, "%Y-%m-%d")
    except ValueError:
        return None
    for date_str, mult in _SPLIT_CACHE.get(tic, ()):
        try:
            if abs((datetime.strptime(date_str, "%Y-%m-%d") - target).days) <= window_days:
                return mult
        except ValueError:
            continue
    return None


def reset_cache():
    """Test hook."""
    global _CACHE_LOADED, _CACHE_WINDOW
    with _LOCK:
        _SPLIT_CACHE.clear()
        _CACHE_LOADED = False
        _CACHE_WINDOW = None
