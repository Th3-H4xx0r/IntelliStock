"""The BacktestResults split: metadata row + step rows + hot progress row.

Today three concurrent writers rewrite one multi-MB document: a heartbeat
every 15s re-sending the last 500 log lines (broker.py:12173-12193), a
progress writer every +2% rewriting backtest_decisions and backtest_refusals
IN THEIR ENTIRETY (broker.py:17802-17866), and a terminal write
(broker.py:12933/12936). The live sample is 3.1 MB while still running, 89% of
it backtest_decisions. On Postgres that is ~8 GB of WAL and ~4M dead TOAST
chunks per backtest.

So: BacktestResults.doc keeps every key that is not one of the six step
arrays, verbatim; the arrays become insert-only BacktestSteps rows; and
status/progress/time_elapsed_seconds/_last_active live in a hot
BacktestProgress row. assemble() puts the legacy document back together byte
for byte.

Three conventions the rest of the code depends on:

  * ``seq`` starts at 1. For ``final=true`` rows, ``seq = 0`` is a marker row
    with a JSON-null doc, so "finalized with zero entries" (a stopped run's
    five empty arrays) is distinguishable from "never finalized".
  * Writers append UNCAPPED entries; assemble applies the legacy caps at read
    time -- trades tail-1000, logs tail-500, portfolio history downsampled to
    3000 -- which reproduces exactly what the legacy writer stored.
  * A key that was never written stays ABSENT, except the four arrays the
    stub creates empty. Live documents prove this matters: a stopped, errored
    or finished run carries no ``backtest_refusals`` key at all.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Optional, Sequence

from db import store
from db.errors import StoreError

RESULTS_TABLE = "BacktestResults"
STEPS_TABLE = "BacktestSteps"
PROGRESS_TABLE = "BacktestProgress"

_STEP_KEYS = (           # order fixed; drives the SELECT, not the output order
    ("decision", "backtest_decisions", None),
    ("refusal",  "backtest_refusals",  None),
    ("trade",    "backtest_trades",    ("tail", 1000)),
    ("pv",       "portfolio_value_history", ("downsample", 3000)),
    ("log",      "logs",               ("tail", 500)),
    ("price",    "backtest_prices",    None),
)
STEP_KINDS = tuple(kind for kind, _, _ in _STEP_KEYS)
KEY_FOR_KIND = {kind: key for kind, key, _ in _STEP_KEYS}
KIND_FOR_KEY = {key: kind for kind, key, _ in _STEP_KEYS}
_CAP_FOR_KIND = {kind: cap for kind, _, cap in _STEP_KEYS}

# The four arrays the stub creates empty at backtest_engine.py:952-955, so
# they exist from the first read exactly as today. backtest_decisions and
# backtest_refusals appear only once written, matching today.
_ALWAYS_PRESENT = {"portfolio_value_history", "backtest_trades",
                   "backtest_prices", "logs"}

# The keys the hot row owns. They stay in doc too (unchanged); the overlay
# just wins on read, so a stale doc status can never beat the hot row.
_PROGRESS_KEYS = ("status", "progress", "time_elapsed_seconds", "_last_active")


def _bid(backtest_id) -> str:
    return store.coerce_id(RESULTS_TABLE, backtest_id)


def _dumps(value: Any) -> str:
    from db import json as dbjson
    return dbjson.dumps(value)


# ---- split ---------------------------------------------------------------

def split_doc(doc: dict):
    """(metadata_doc, {kind: [entry, ...]}, progress_payload)."""
    if not isinstance(doc, dict) or doc.get("id") is None:
        raise StoreError("BacktestResults document needs an id")
    meta = {k: v for k, v in doc.items() if k not in KIND_FOR_KEY}
    steps = {}
    for kind, key, _cap in _STEP_KEYS:
        if key in doc:
            steps[kind] = list(doc[key] or [])
    progress = {k: doc[k] for k in _PROGRESS_KEYS if k in doc}
    return meta, steps, progress


def write_split(doc: dict, *, final: bool) -> None:
    """Write a whole legacy document into the three tables.

    Used by the terminal writer, by the migration script, and by tests. The
    incremental writers use append_steps/write_progress instead.
    """
    meta, steps, progress = split_doc(doc)
    store.insert(RESULTS_TABLE, meta, conflict="replace")
    for kind in STEP_KINDS:
        entries = steps.get(kind)
        if entries is None:
            continue
        if final:
            finalize_steps(doc["id"], kind, entries)
        else:
            append_steps(doc["id"], kind, entries, start_seq=0)
    if progress:
        write_progress(doc["id"], progress)


# ---- steps ---------------------------------------------------------------

def append_steps(backtest_id, kind: str, entries: Sequence, *,
                 start_seq: int, final: bool = False) -> int:
    """Append entries at seq = start_seq+1 ... and return the new watermark."""
    if kind not in KEY_FOR_KIND:
        raise StoreError("unknown step kind %r" % kind)
    rows = list(entries)
    if not rows:
        return start_seq
    bid = _bid(backtest_id)
    seqs = [start_seq + offset for offset in range(1, len(rows) + 1)]
    store.sql(
        'INSERT INTO "BacktestSteps" (backtest_id, kind, seq, final, doc) '
        "SELECT %s, %s, s, %s, d FROM unnest("
        "  %s::bigint[], %s::jsonb[]"
        ") AS t(s, d) "
        "ON CONFLICT (backtest_id, kind, final, seq) DO NOTHING",
        (bid, kind, bool(final), seqs, [_dumps(e) for e in rows]))
    return start_seq + len(rows)


def finalize_steps(backtest_id, kind: str, entries: Sequence) -> None:
    """Write the authoritative array for one kind, plus the seq=0 marker."""
    if kind not in KEY_FOR_KIND:
        raise StoreError("unknown step kind %r" % kind)
    bid = _bid(backtest_id)
    store.sql('DELETE FROM "BacktestSteps" WHERE backtest_id = %s '
              "AND kind = %s AND final", (bid, kind))
    store.sql('INSERT INTO "BacktestSteps" (backtest_id, kind, seq, final, doc) '
              "VALUES (%s, %s, 0, true, 'null'::jsonb)", (bid, kind))
    append_steps(backtest_id, kind, entries, start_seq=0, final=True)


def watermarks(backtest_id) -> dict:
    """kind -> max(seq) over the LIVE rows. A reconnecting writer re-reads
    this so it never duplicates or skips."""
    rows = store.sql('SELECT kind, max(seq) AS m FROM "BacktestSteps" '
                     "WHERE backtest_id = %s AND NOT final GROUP BY kind",
                     (_bid(backtest_id),))
    return {r["kind"]: int(r["m"]) for r in rows}


def _fetch_steps(backtest_id) -> dict:
    """{kind: (final_entries, live_entries, has_final)}, ordered by seq."""
    out = {kind: ([], [], False) for kind in STEP_KINDS}
    rows = store.sql('SELECT kind, seq, final, doc FROM "BacktestSteps" '
                     "WHERE backtest_id = %s ORDER BY kind, final, seq",
                     (_bid(backtest_id),))
    for row in rows:
        kind = row["kind"]
        if kind not in out:
            continue
        final_entries, live_entries, has_final = out[kind]
        if row["final"]:
            has_final = True
            if row["seq"] > 0:
                final_entries.append(row["doc"])
        else:
            live_entries.append(row["doc"])
        out[kind] = (final_entries, live_entries, has_final)
    return out


def _apply_cap(values: list, cap) -> list:
    if cap is None:
        return values
    mode, n = cap
    if mode == "tail":
        return values[-n:]
    if mode == "downsample":
        # Keep the true start and the shape, do not tail-slice: a long,
        # high-cadence RUNNING backtest must show the real start value.
        from broker_snapshot_helpers import downsample_history
        return list(downsample_history(values, n))
    raise StoreError("unknown cap %r" % (cap,))


# ---- progress ------------------------------------------------------------

def write_progress(backtest_id, payload: dict, *, last_active=None) -> None:
    """Upsert the hot row. ``payload`` carries the legacy scalar VALUES
    verbatim, so their JSON types survive (a stopped run's progress is an int,
    a running run's is a float)."""
    bid = _bid(backtest_id)
    when = last_active
    if when is None and payload.get("_last_active"):
        try:
            when = _dt.datetime.fromisoformat(str(payload["_last_active"]))
        except ValueError:
            when = None
    store.sql(
        'INSERT INTO "BacktestProgress" (id, payload, last_active) '
        "VALUES (%s, %s::jsonb, %s) "
        "ON CONFLICT (id) DO UPDATE SET "
        '  payload = jsonb_deep_merge("BacktestProgress".payload, EXCLUDED.payload), '
        "  last_active = coalesce(EXCLUDED.last_active, "
        '                         "BacktestProgress".last_active), '
        "  updated_at = now()",
        (bid, _dumps(payload), when))


def write_stub(stub: dict) -> None:
    """The row-creation write: metadata WITHOUT the six arrays, plus the hot
    progress row. Resets nothing -- backtest_engine.py:1126-1127 calls it
    twice in a row (pending, then running), and a broker may already have
    appended steps by then.
    """
    meta, _steps, _progress = split_doc(stub)
    store.insert(RESULTS_TABLE, meta, conflict="replace")
    write_progress(stub["id"], {k: stub.get(k) for k in
                                ("status", "progress", "time_elapsed_seconds")
                                if k in stub})


def heartbeat(backtest_id, *, last_active: str, elapsed_seconds=None,
              new_log_lines: Sequence = (), log_seq: int = 0) -> int:
    """The 15-second liveness write.

    The legacy version re-sent the whole last-500-line log list every tick
    (broker.py:12191). This writes two scalars into the hot row and appends
    only the lines past ``log_seq``; assemble() still tails 500 on read, so
    the document the UI sees is unchanged.

    Returns the new log watermark. A writer that has lost its in-process
    watermark re-seeds it from watermarks(backtest_id).

    The heartbeat is the ONLY writer of the ``log`` kind: the log buffer is
    trimmed FIFO to 500 lines, so slicing it correctly needs the logger's
    monotonic emitted counter, and exactly one writer may own that watermark.
    """
    payload = {"_last_active": last_active}
    if elapsed_seconds is not None:
        payload["time_elapsed_seconds"] = elapsed_seconds
    write_progress(backtest_id, payload)
    if new_log_lines:
        return append_steps(backtest_id, "log", new_log_lines, start_seq=log_seq)
    return log_seq


def write_progress_tick(backtest_id, *, hot: dict, metadata: dict,
                        appended, seqs) -> dict:
    """The +2%-of-progress write.

    The legacy version rewrote backtest_trades (last 1000), the downsampled
    portfolio history, the last 500 logs, and backtest_decisions +
    backtest_refusals IN THEIR ENTIRETY every tick -- 2.7 MB on the live
    sample, growing with every tick. This writes four scalars to the hot row,
    a small deep-merge patch to doc, and only the entries past each kind's
    watermark.

    ``appended`` maps a kind to its FULL uncapped source list; the caps are
    applied by assemble() on read. It may NOT carry ``log``: the log buffer is
    trimmed FIFO to 500 lines, so slicing it correctly needs the logger's
    monotonic emitted counter and exactly one writer -- the heartbeat -- may
    own that watermark.
    """
    if "log" in (appended or {}):
        raise StoreError(
            "write_progress_tick may not append the 'log' kind: the heartbeat "
            "is its only writer (the buffer is trimmed FIFO, so two owners "
            "would duplicate or drop lines)")
    if hot:
        write_progress(backtest_id, hot)
    if metadata:
        store.update(RESULTS_TABLE, backtest_id, metadata)
    for kind, values in (appended or {}).items():
        written = int(seqs.get(kind, 0))
        rows = list(values or [])
        if len(rows) > written:
            seqs[kind] = append_steps(backtest_id, kind, rows[written:],
                                      start_seq=written)
        else:
            seqs.setdefault(kind, written)
    return dict(seqs)


def write_difficulty(backtest_id, difficulty) -> None:
    """Patch the ``difficulty`` scalar onto the metadata document.

    The engine computes it once, right after the stub write; broker.py:12037
    reads it back to carry it onto the running stub, and broker.py:17909 puts
    it in the Discord embed. A deep-merge patch, not a replace: the stub the
    engine just wrote must survive.
    """
    store.update(RESULTS_TABLE, backtest_id, {"difficulty": difficulty})


def write_terminal(backtest_id, result: dict, *,
                   insert_if_absent: bool = False) -> None:
    """The end-of-run write.

    Metadata is deep-merged into doc (the legacy call was ``.update(...)``,
    which merges); each array present in ``result`` is finalized into
    BacktestSteps with final=true, superseding the live rows; the hot row is
    set to the terminal status.

    assert_secret_free() is the CALLER's responsibility and stays at
    broker.py's terminal write, unchanged.
    """
    meta, steps, progress = split_doc(
        dict(result, id=result.get("id", backtest_id)))
    if insert_if_absent and store.get(RESULTS_TABLE, backtest_id) is None:
        store.insert(RESULTS_TABLE, meta, conflict="replace")
    else:
        store.update(RESULTS_TABLE, backtest_id, meta)
    for kind in STEP_KINDS:
        if kind in steps:
            finalize_steps(backtest_id, kind, steps[kind])
    if progress:
        write_progress(backtest_id, progress)


def set_status(backtest_id, status: str, *, timestamp: Optional[str] = None,
               extra_metadata: Optional[dict] = None) -> None:
    """Status goes to the hot row; timestamp and anything else deep-merges
    into doc. This is the stop/error/pause path."""
    write_progress(backtest_id, {"status": status})
    patch = dict(extra_metadata or {})
    if timestamp is not None:
        patch["timestamp"] = timestamp
    if patch:
        store.update(RESULTS_TABLE, backtest_id, patch)


def patch_metadata(backtest_id, patch: dict) -> None:
    """Deep-merge into doc. Never touches the hot row or the steps.

    ``{"k": None}`` sets JSON null, it does not delete the key -- which is
    what broker.py's nexus_lookback clear expects.
    """
    if patch:
        store.update(RESULTS_TABLE, backtest_id, patch)


def read_status(backtest_id) -> Optional[str]:
    """The run's status, hot row first (R4: doc.status is advisory after the
    split). Falls back to doc only when no hot row exists, which is exactly
    the precedence assemble() applies."""
    progress = read_progress(backtest_id)
    if progress is not None and "status" in progress:
        return progress.get("status")
    row = store.get(RESULTS_TABLE, backtest_id)
    return None if row is None else row.get("status")


def stop_running(ids: Optional[Sequence] = None) -> int:
    """Mark every run the hot row still calls ``running`` as ``stopped`` and
    return how many rows changed.

    The stop-all sweep (interactive_utils) used to be
    ``filter(status == "running").update(status = "stopped")`` on the
    multi-MB document. Under R4 that predicate reads BacktestProgress, never
    BacktestResults' stale generated column.
    """
    query = ('UPDATE "BacktestProgress" SET '
             "  payload = jsonb_deep_merge(payload, '{\"status\":\"stopped\"}'::jsonb), "
             "  updated_at = now() "
             "WHERE payload ->> 'status' = 'running'")
    params: tuple = ()
    if ids is not None:
        query += " AND id = ANY(%s)"
        params = ([_bid(i) for i in ids],)
    return len(store.sql(query + " RETURNING id", params))


def resume_from_critical_pause(backtest_id, cleared_fields: dict) -> bool:
    """Compare-and-swap: flip the hot row to "running" and clear the stale
    pause_* metadata ONLY when the status is "paused_llm_critical", so a
    manual pause is never stomped. Replaces the server-side r.branch inside
    .replace(lambda row: ...) the changefeed used to run.
    """
    rows = store.sql(
        'UPDATE "BacktestProgress" SET '
        "  payload = jsonb_deep_merge(payload, '{\"status\":\"running\"}'::jsonb), "
        "  updated_at = now() "
        "WHERE id = %s AND payload ->> 'status' = 'paused_llm_critical' "
        "RETURNING id",
        (_bid(backtest_id),))
    if not rows:
        return False
    patch = dict(cleared_fields or {})
    patch["resumed_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    store.update(RESULTS_TABLE, backtest_id, patch)
    return True


def active_run_age(backtest_id) -> Optional[float]:
    """Seconds since the last heartbeat of a run the hot row still calls
    ``running``, or None when no live-run evidence exists.

    The duplicate-run guard (broker.py:12029) used to read ``status`` and
    ``_last_active`` off the multi-MB document; both now live on the hot row
    (R4: doc.status is advisory after the split). An unparseable timestamp
    returns 9999, matching the legacy guard's "stale, overwrite" fallback --
    a parse failure must never look like a live duplicate.
    """
    progress = read_progress(backtest_id) or {}
    if progress.get("status") != "running":
        return None
    last_active = progress.get("_last_active") or ""
    if not last_active:
        return None
    try:
        when = _dt.datetime.fromisoformat(str(last_active).replace("Z", "+00:00"))
        now = _dt.datetime.now(_dt.timezone.utc)
        return (now - when).total_seconds()
    except Exception:
        return 9999


def read_progress(backtest_id) -> Optional[dict]:
    rows = store.sql('SELECT payload FROM "BacktestProgress" WHERE id = %s',
                     (_bid(backtest_id),))
    return rows[0]["payload"] if rows else None


# ---- assemble ------------------------------------------------------------

def assemble(backtest_id) -> Optional[dict]:
    """Reconstruct the legacy JSON document, byte for byte."""
    row = store.get(RESULTS_TABLE, backtest_id)
    if row is None:
        return None
    doc = dict(row)                                  # verbatim metadata
    steps = _fetch_steps(backtest_id)
    for kind, key, cap in _STEP_KEYS:
        final_entries, live_entries, has_final = steps.get(kind, ([], [], False))
        if has_final:                                # terminal write happened
            values = list(final_entries)             # ORDER BY seq, no cap
        elif live_entries:
            values = _apply_cap(list(live_entries), cap)
        elif key in _ALWAYS_PRESENT:
            values = []
        else:
            continue                                 # absent, matching today
        doc[key] = values
    progress = read_progress(backtest_id)
    if progress:                                     # hot row wins over doc
        for key in _PROGRESS_KEYS:
            if key in progress:
                doc[key] = progress[key]
    # Lexicographic key order, matching RethinkDB's keys(), so a naive
    # json.dumps(doc) produces the same bytes as before. Every fingerprint
    # independently goes through db.json.canonical, so this is belt and
    # braces, not the guarantee.
    return dict(sorted(doc.items()))


def assemble_field(backtest_id, key: str):
    """One step array, capped the same way assemble() would cap it, without
    paying for the whole document. interactive_utils.py:6841 reads only
    portfolio_value_history."""
    kind = KIND_FOR_KEY.get(key)
    if kind is None:
        doc = assemble(backtest_id)
        return None if doc is None else doc.get(key)
    final_entries, live_entries, has_final = _fetch_steps(backtest_id).get(
        kind, ([], [], False))
    if has_final:
        return list(final_entries)
    if live_entries:
        return _apply_cap(list(live_entries), _CAP_FOR_KIND[kind])
    return [] if key in _ALWAYS_PRESENT else None


def delete_backtest(backtest_id) -> bool:
    """Remove the row from all three tables. False when nothing was there."""
    bid = _bid(backtest_id)
    existed = store.get(RESULTS_TABLE, backtest_id) is not None
    store.sql('DELETE FROM "BacktestSteps" WHERE backtest_id = %s', (bid,))
    store.sql('DELETE FROM "BacktestProgress" WHERE id = %s', (bid,))
    store.delete(RESULTS_TABLE, backtest_id)
    return existed
