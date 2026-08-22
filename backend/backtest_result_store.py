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
