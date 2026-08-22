"""Change watchers over LISTEN/NOTIFY with a poll backstop.

Delivers ReQL-shaped changes -- ``{"old_val": doc|None, "new_val": doc|None}``
-- so handlers written against ReQL changefeeds port with no shape edit.

Three properties the RethinkDB feeds do not have:

  * the watcher re-reads current state on start AND on every reconnect, so a
    stop issued while disconnected is no longer silently dropped (8 of the 23
    sites lose events today; 3 have no reconnect at all);
  * a poll every poll_interval seconds re-reads and diffs regardless of
    notifications, so a missed NOTIFY costs at most one interval;
  * a handler exception is logged and swallowed -- the watcher never dies.

The NOTIFY payload is the row id only (never data: the payload cap is 8000
bytes), so old_val comes from the watcher's per-id cache.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Iterator, Optional, Sequence

from . import pool as dbpool
from . import schema as dbschema
from . import store as dbstore

Change = dict

DEFAULT_POLL = float(os.environ.get("DB_WATCH_POLL_SECONDS", "2.0"))
_INITIAL_BACKOFF = 2.0
_MAX_BACKOFF = 30.0


def _project(doc, fields):
    if doc is None or not fields:
        return doc
    return {k: doc[k] for k in fields if k in doc}


def _pk_field(table: str) -> str:
    return dbschema.spec(table).pk_field


class Watcher:
    """One daemon thread: LISTEN + poll + diff for one watched set."""

    def __init__(self, table: str, on_change: Callable[[Change], None], *,
                 label: str, row_id: Any = None, predicate: Any = None,
                 fields: Optional[Sequence] = None, include_initial: bool = True,
                 squash: bool = False, squash_window: float = 1.0,
                 poll_interval: float = DEFAULT_POLL, log=None,
                 should_continue: Optional[Callable[[], bool]] = None) -> None:
        self.table = table
        self.on_change = on_change
        self.label = label
        self.row_id = row_id
        self.predicate = predicate
        self.fields = tuple(fields) if fields else None
        self.include_initial = include_initial
        self.squash = squash
        self.squash_window = squash_window
        self.poll_interval = poll_interval
        self.log = log
        self._cont = should_continue or (lambda: True)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._cache: dict = {}
        self._seeded = False
        self._pending: dict = {}          # id -> (oldest_old_val, deadline)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="watch:%s" % self.label)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _log(self, msg: str, color: str = "yellow") -> None:
        if self.log is None:
            return
        try:
            self.log("%s: %s" % (self.label, msg), color, service="Postgres")
        except Exception:
            pass

    def _snapshot(self) -> dict:
        """Current state of the watched set, keyed by row id.

        BacktestProgress has no ``doc`` column -- its authoritative state is
        ``payload`` plus generated columns -- so it is read with SELECT * and
        the row itself is the "document". It is the only table in the repo
        with that shape.
        """
        spec_ = dbschema.spec(self.table)
        has_doc = dbschema.has_doc_column(spec_)
        q = dbschema.quoted(self.table)
        if self.row_id is not None:
            key = dbstore.coerce_id(self.table, self.row_id)
            if not has_doc:
                rows = dbstore.sql("SELECT * FROM %s WHERE id = %%s" % q, (key,))
                return {key: dict(rows[0])} if rows else {}
            doc = dbstore.get(self.table, self.row_id)
            return {key: doc} if doc is not None else {}
        if not has_doc:
            rows = dbstore.sql("SELECT * FROM %s" % q)
            return {str(r.get("id")): dict(r) for r in rows}
        if self.predicate is not None:
            rows = dbstore.run(dbstore.filter(self.table, self.predicate))
        else:
            rows = dbstore.run(dbstore.Selection(self.table))
        pk_field = _pk_field(self.table)
        return {str(r.get(pk_field)): r for r in rows}

    def _deliver(self, old_val, new_val) -> None:
        change = {"old_val": _project(old_val, self.fields),
                  "new_val": _project(new_val, self.fields)}
        try:
            self.on_change(change)
        except Exception as exc:            # never let a handler kill the feed
            self._log("handler error: %s: %s" % (type(exc).__name__, exc), "red")

    def _emit(self, key: str, old_val, new_val) -> bool:
        """Returns True when something was actually delivered."""
        if old_val == new_val:
            return False
        if not self.squash:
            self._deliver(old_val, new_val)
            return True
        oldest, _ = self._pending.get(key, (old_val, 0.0))
        self._pending[key] = (oldest, time.time() + self.squash_window)
        return False

    def _flush_squashed(self, *, force: bool = False) -> bool:
        if not self._pending:
            return False
        now = time.time()
        delivered = False
        for key in list(self._pending):
            oldest, deadline = self._pending[key]
            if not force and now < deadline:
                continue
            del self._pending[key]
            self._deliver(oldest, self._cache.get(key))
            delivered = True
        return delivered

    def _diff(self, snapshot: dict) -> bool:
        delivered = False
        for key, new_val in snapshot.items():
            old_val = self._cache.get(key)
            if self._emit(key, old_val, new_val):
                delivered = True
            self._cache[key] = new_val
        for key in list(self._cache):
            if key not in snapshot:
                # broker.py:5833 needs new_val is None to mean "row deleted".
                if self._emit(key, self._cache[key], None):
                    delivered = True
                del self._cache[key]
        return delivered

    def _resync(self) -> bool:
        snapshot = self._snapshot()
        if not self._seeded and not self.include_initial:
            # kalshi/backtest_worker.py:256 seeds silently.
            self._cache = dict(snapshot)
            self._seeded = True
            return False
        self._seeded = True
        return self._diff(snapshot)

    def _run(self) -> None:
        backoff = _INITIAL_BACKOFF
        while self._cont() and not self._stop.is_set():
            conn = None
            try:
                conn = dbpool.listen_connection()
                conn.execute('LISTEN "tbl:%s"' % self.table)
                if self._resync():
                    backoff = _INITIAL_BACKOFF
                self._flush_squashed()
                while self._cont() and not self._stop.is_set():
                    # A finite timeout makes notifies() return, which is the
                    # resync tick; the poll backstop lives here.
                    got_any = False
                    for _notify in conn.notifies(timeout=self.poll_interval,
                                                 stop_after=1):
                        got_any = True
                    if self._stop.is_set():
                        break
                    if self._resync():
                        backoff = _INITIAL_BACKOFF
                    if self._flush_squashed(force=not got_any):
                        backoff = _INITIAL_BACKOFF
                    if conn.closed:
                        raise RuntimeError("listen connection closed")
            except Exception as exc:
                if not self._stop.is_set():
                    self._log("connection lost (%s); reconnecting in %.0fs"
                              % (exc, backoff))
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
            if self._stop.is_set() or not self._cont():
                break
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 1.5, _MAX_BACKOFF)


def watch_row(table: str, row_id, on_change, *, label: str,
              include_initial: bool = True, squash: bool = False,
              squash_window: float = 1.0, poll_interval: float = DEFAULT_POLL,
              log=None, should_continue=None) -> Watcher:
    return Watcher(table, on_change, label=label, row_id=row_id,
                   include_initial=include_initial, squash=squash,
                   squash_window=squash_window, poll_interval=poll_interval,
                   log=log, should_continue=should_continue)


def watch_table(table: str, on_change, *, label: str,
                fields: Optional[Sequence] = None, include_initial: bool = True,
                squash: bool = False, squash_window: float = 1.0,
                poll_interval: float = DEFAULT_POLL, log=None,
                should_continue=None) -> Watcher:
    return Watcher(table, on_change, label=label, fields=fields,
                   include_initial=include_initial, squash=squash,
                   squash_window=squash_window, poll_interval=poll_interval,
                   log=log, should_continue=should_continue)


def watch_filter(table: str, predicate, on_change, *, label: str,
                 **kwargs) -> Watcher:
    return Watcher(table, on_change, label=label, predicate=predicate, **kwargs)


def feed(table: str, *, row_id=None, predicate=None, fields=None,
         include_initial: bool = True, squash: bool = False,
         poll_interval: float = DEFAULT_POLL,
         should_continue=None) -> Iterator:
    """A blocking generator over the same Change dicts a Watcher delivers.

    This is what ``run_reconnecting_changefeed``'s ``open_feed(conn)`` returns
    after the port: callers write ``lambda c: watch.feed(T,
    include_initial=True)`` instead of
    ``lambda c: r.db(DB).table(T).changes().run(c)``.
    """
    import queue as _queue
    q: "_queue.Queue" = _queue.Queue()
    w = Watcher(table, q.put, label="feed:%s" % table, row_id=row_id,
                predicate=predicate, fields=fields,
                include_initial=include_initial, squash=squash,
                poll_interval=poll_interval, should_continue=should_continue)
    w.start()
    try:
        while True:
            try:
                yield q.get(timeout=poll_interval)
            except _queue.Empty:
                if not w.is_alive():
                    return
    finally:
        w.stop()
