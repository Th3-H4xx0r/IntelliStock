"""Adapter: watch BacktestProgress and deliver ReQL-shaped (id, status) changes.

self_learning_engine.py used to open a changefeed on the whole
BacktestResults table with a server-side ``.pluck({"new_val": ["id","status"]})``,
because "without it new_val is the whole 5-13MB document on every progress
tick of a running backtest" (:529-530). After the split the hot row IS that
projection, so this is a plain watch on BacktestProgress and the projection
stops being an optimisation at all.

BacktestProgress has no ``doc`` column -- its state is ``payload`` plus the
STORED generated columns over it -- so this adapter reads those columns and
hands the handler the same two-key dict the pluck produced.
"""
from __future__ import annotations

from typing import Callable, Iterator, Optional

from db import store, watch

TABLE = "BacktestProgress"

#: The two keys the old server-side pluck emitted, and the only two the
#: self-learning handler reads.
FIELDS = ("id", "status")


def _project(row: Optional[dict]) -> Optional[dict]:
    """A watched row reduced to exactly {"id", "status"}.

    Both keys are always present (``None`` when the row has no status), which
    is what ``.pluck`` produced for a document whose ``status`` was set on
    every write that mattered here.
    """
    if row is None:
        return None
    return {"id": row.get("id"), "status": row.get("status")}


def current_rows() -> list:
    """Every hot row as {"id", "status"} -- the poll shape, kept for callers
    that want a snapshot rather than a feed."""
    return [_project(r) for r in store.sql(
        'SELECT id, payload ->> \'status\' AS status FROM "BacktestProgress"')]


def _adapter(on_change: Callable[[dict], None]) -> Callable[[dict], None]:
    def _adapt(change: dict) -> None:
        change = change or {}
        on_change({"old_val": _project(change.get("old_val")),
                   "new_val": _project(change.get("new_val"))})
    return _adapt


def watch_progress_rows(on_change: Callable[[dict], None], *, label: str,
                        log=None, should_continue=None) -> watch.Watcher:
    """A Watcher over BacktestProgress delivering {"old_val", "new_val"} with
    only ``id`` and ``status`` populated.

    ``include_initial=True`` replays every row on start and on every
    reconnect; the persisted ``processed_run_ids`` watermark in
    ``self_learning/store.py`` dedupes, exactly as it does today.
    """
    return watch.watch_table(
        TABLE, _adapter(on_change), label=label, include_initial=True,
        squash=True, log=log, should_continue=should_continue)


def progress_feed(*, should_continue=None) -> Iterator[dict]:
    """The blocking-generator form, for ``run_reconnecting_changefeed``.

    ``open_feed(conn)`` wants an iterator of Change dicts; this yields the same
    two-key projection ``watch_progress_rows`` delivers, so the engine's
    reconnect loop and its connection lifetime are untouched by the port.
    """
    for change in watch.feed(TABLE, include_initial=True, squash=True,
                             fields=FIELDS, should_continue=should_continue):
        yield {"old_val": _project((change or {}).get("old_val")),
               "new_val": _project((change or {}).get("new_val"))}
