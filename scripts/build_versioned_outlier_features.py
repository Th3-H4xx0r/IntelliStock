#!/usr/bin/env python3
"""Prepare versioned cross-sections from a complete provider archive.

This prepares observations only. Performance evaluation belongs to the native
IntelliStock API. Eligibility is evaluated separately on every historical day;
current liquidity, price and tradability never select the historical universe.
Provider security-master omissions and undated instrument classifications are
recorded explicitly, so this is not a certified survivorship-free dataset.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from outlier_features import FEATURES_TABLE, compute_features, feature_id


def symbol_rows(symbol, adjusted, raw, dataset, adv_min=1e7, price_min=3.0, _retain=False, liquidity=None, reset_dates=()):
    """Retain a name if it ever qualifies, without changing any past rank.

    This optimization cannot exclude a historically eligible observation:
    ranks below use only that day's eligibility, and retained history supplies
    exit features after a once-liquid security deteriorates.
    """
    def keyed(bars):
        out = {str(b["t"])[:10]: b for b in bars}
        if len(out) != len(bars):
            raise ValueError(f"{symbol}: duplicate date")
        return out

    a, r = keyed(adjusted), keyed(raw)
    if set(a) != set(r):
        raise ValueError(f"{symbol}: raw/adjusted date mismatch")
    dates = sorted(a)
    if not dates:
        return []
    boundaries = [i for i in range(1, len(dates))
                  if (date.fromisoformat(dates[i]) - date.fromisoformat(dates[i - 1])).days > 30
                  or any(dates[i - 1] < reset <= dates[i] for reset in reset_dates)]
    if boundaries:
        rows, first = [], 0
        for stop in boundaries + [len(dates)]:
            block = dates[first:stop]
            rows.extend(symbol_rows(symbol, [a[d] for d in block], [r[d] for d in block],
                                    dataset, adv_min, price_min, _retain=True,
                                    liquidity=([b for b in liquidity if block[0] <= str(b["t"])[:10] <= block[-1]]
                                               if liquidity is not None else None)))
            first = stop
        return rows if _retain or any(row["rank_eligible"] for row in rows) else []
    liquidity_adv = None
    if liquidity is not None:
        liquidity_adv, dollar_window, running = {}, deque(), 0.0
        liquidity_days = keyed(liquidity)
        for day in sorted(liquidity_days):
            b = liquidity_days[day]
            close, volume = float(b["c"]), float(b["v"])
            if not math.isfinite(close) or not math.isfinite(volume) or close <= 0 or volume < 0:
                raise ValueError(f"{symbol}: invalid consolidated liquidity observation")
            value = close * volume
            dollar_window.append(value)
            running += value
            if len(dollar_window) > 20:
                running -= dollar_window.popleft()
            liquidity_adv[day] = running / 20 if len(dollar_window) == 20 else 0.0
    advs, nominal, eligible = [], [], []
    window, total = deque(), 0.0
    for i, day in enumerate(dates):
        close, volume = float(r[day]["c"]), float(r[day]["v"])
        adjusted_close = float(a[day]["c"])
        if not all(math.isfinite(v) for v in (close, volume, adjusted_close)):
            raise ValueError(f"{symbol}: nonfinite observation")
        if close <= 0 or adjusted_close <= 0 or volume < 0:
            raise ValueError(f"{symbol}: invalid price/volume")
        dollar_volume = close * volume
        window.append(dollar_volume)
        total += dollar_volume
        if len(window) > 20:
            total -= window.popleft()
        adv = (liquidity_adv.get(day, 0.0) if liquidity_adv is not None
               else total / len(window))
        advs.append(adv)
        nominal.append(close)
        eligible.append(i >= 126 and adv >= adv_min and close >= price_min)
    if not _retain and not any(eligible):
        return []
    rows = compute_features([float(a[d]["c"]) for d in dates],
                            [float(a[d]["v"]) for d in dates], dates)
    for i, row in enumerate(rows):
        row.update(id=feature_id(row["date"], symbol, dataset), symbol=symbol,
                   nominal_close=nominal[i], adv20=advs[i],
                   raw_volume=float(r[dates[i]]["v"]),
                   rank_eligible=eligible[i], rs_rank=None)
    return rows


def rank_session(rows, aliases=()):
    """Equal-weight, tie-aware ranks inside this session's eligible universe."""
    eligible = {r["symbol"]: r for r in rows if r.get("rank_eligible")}
    parent = {s: s for s in eligible}
    incoming = {}

    def root(symbol):
        while parent[symbol] != symbol:
            parent[symbol] = parent[parent[symbol]]
            symbol = parent[symbol]
        return symbol

    for event in aliases:
        old, new = event["old_symbol"], event["new_symbol"]
        if (old == new or old not in eligible or new not in eligible
                or not event.get("old_cusip")
                or event.get("old_cusip") != event.get("new_cusip")):
            continue
        a, b = eligible[old], eligible[new]
        if a["date"] >= event["process_date"] or a["date"] != b["date"]:
            continue
        # Same security identity AND same observed price/volume. Distinct
        # predecessors and share classes remain separate. If only one alias
        # qualifies, it remains eligible regardless of successor coverage.
        if not all(a.get(k) is not None and a[k] == b.get(k)
                   for k in ("nominal_close", "raw_volume")):
            continue
        parent[root(old)] = root(new)
        incoming[new] = max(incoming.get(new, ""), event["process_date"])
    groups = {}
    for symbol in eligible:
        groups.setdefault(root(symbol), []).append(symbol)
    for members in groups.values():
        if len(members) < 2:
            continue
        survivor = max(members, key=lambda s: (incoming.get(s, ""), s))
        for symbol in members:
            if symbol != survivor:
                eligible[symbol].update(rank_eligible=False, rank_duplicate_of=survivor)
    liquid = sorted((r for r in rows if r.get("rank_eligible")
                     and r.get("ret126") is not None), key=lambda r: float(r["ret126"]))
    for row in rows:
        row["rs_rank"] = None
    n, first = len(liquid), 0
    while first < n:
        last = first + 1
        while last < n and float(liquid[last]["ret126"]) == float(liquid[first]["ret126"]):
            last += 1
        rank = ((first + last - 1) / 2 / (n - 1)) if n > 1 else 1.0
        for row in liquid[first:last]:
            row["rs_rank"] = rank
        first = last
    return rows


def publish_rows(store, dataset, batches, metadata):
    """Publish a fresh immutable namespace, completing its manifest last.

    A failed publication stays incomplete and cannot be selected by the
    strategy. Use a new version for retries; never overwrite existing rows.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]+", dataset):
        raise ValueError("invalid dataset name")
    key = f"outlier:{dataset}"
    if store.get("PointInTimeDatasetSnapshots", key) is not None:
        raise ValueError(f"dataset already exists: {dataset}")
    manifest = {**metadata, "id": key, "kind": "outlier_features", "complete": False}
    result = store.insert("PointInTimeDatasetSnapshots", manifest, conflict="error")
    if result.errors:
        raise RuntimeError("dataset manifest insert failed")
    dates, count = set(), 0
    insert = getattr(store, "insert_bulk", store.insert)
    for rows in batches:
        for row in rows:
            if row["id"] != feature_id(row["date"], row["symbol"], dataset):
                raise ValueError("row outside dataset namespace")
        result = insert(FEATURES_TABLE, rows, conflict="error")
        if result.errors or result.inserted != len(rows):
            raise RuntimeError("dataset row insert incomplete")
        dates.update(r["date"] for r in rows)
        count += len(rows)
    if not count:
        raise ValueError("empty dataset")
    store.update("PointInTimeDatasetSnapshots", key,
                 {"complete": True, "rows": count, "dates": sorted(dates),
                  "published_at": datetime.now(timezone.utc).isoformat()})
    confirmed = store.get("PointInTimeDatasetSnapshots", key)
    if not confirmed or confirmed.get("complete") is not True or confirmed.get("rows") != count:
        raise RuntimeError("manifest completion verification failed")
    return confirmed


def prepare(archive, output, dataset, start, liquidity_archive=None, identity_events=None, history_resets=None):
    """Resumable observation preparation; SQLite is a feature spool only."""
    manifest = json.loads((archive / "manifest.json").read_text())
    settings = {"archive": manifest, "dataset": dataset, "start": start,
                "adv_min": 1e7, "price_min": 3.0,
                "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    aliases = json.loads(identity_events.read_text()) if identity_events is not None else []
    resets = json.loads(history_resets.read_text()) if history_resets is not None else {}
    if identity_events is not None:
        settings["identity_events_sha256"] = hashlib.sha256(identity_events.read_bytes()).hexdigest()
    if history_resets is not None:
        settings["history_resets"] = resets
    if liquidity_archive is not None:
        liquidity_manifest = json.loads((liquidity_archive / "manifest.json").read_text())
        if (liquidity_manifest.get("feed") != "sip" or liquidity_manifest.get("adjustment") != "raw"
                or liquidity_manifest.get("complete") is not True
                or liquidity_manifest.get("symbols") != manifest["symbols"]
                or liquidity_manifest.get("source_manifest_sha256") != hashlib.sha256((archive / "manifest.json").read_bytes()).hexdigest()):
            raise ValueError("consolidated archive incomplete or source mismatch")
        settings["liquidity_archive"] = liquidity_manifest
    output.mkdir(parents=True, exist_ok=True)
    config_path = output / "settings.json"
    if config_path.exists() and json.loads(config_path.read_text()) != settings:
        raise ValueError("preparation settings changed; choose a new output directory")
    config_path.write_text(json.dumps(settings, indent=2))
    db = sqlite3.connect(output / "features.sqlite")
    db.execute("CREATE TABLE IF NOT EXISTS rows (id TEXT PRIMARY KEY, day TEXT, doc TEXT)")
    db.execute("CREATE INDEX IF NOT EXISTS rows_day ON rows(day)")
    db.execute("CREATE TABLE IF NOT EXISTS batches (offset INTEGER PRIMARY KEY, digest TEXT)")
    for offset in range(0, manifest["symbols"], 100):
        paths = [archive / f"{offset:05d}-{adjustment}.json.gz" for adjustment in ("split", "raw")]
        if liquidity_archive is not None:
            paths.append(liquidity_archive / f"{offset:05d}-raw.json.gz")
        if not all(p.exists() for p in paths):
            db.close()
            print(f"Archive not complete; prepared through {offset} symbols", flush=True)
            return False
        digest = hashlib.sha256(b"".join(p.read_bytes() for p in paths)).hexdigest()
        done = db.execute("SELECT digest FROM batches WHERE offset=?", (offset,)).fetchone()
        if done:
            if done[0] != digest:
                raise ValueError("archived input changed")
            continue
        split, raw = [json.load(gzip.open(p, "rt")) for p in paths[:2]]
        liquidity = json.load(gzip.open(paths[2], "rt")) if liquidity_archive is not None else None
        if liquidity is not None and liquidity["requested_symbols"] != raw["requested_symbols"]:
            raise ValueError("consolidated batch requests differ")
        if split["requested_symbols"] != raw["requested_symbols"]:
            raise ValueError("batch requests differ")
        with db:
            for symbol in split["requested_symbols"]:
                rows = symbol_rows(symbol, split["bars"].get(symbol, []),
                                   raw["bars"].get(symbol, []), dataset,
                                   liquidity=(liquidity["bars"].get(symbol, []) if liquidity is not None else None),
                                   reset_dates=resets.get("symbols", {}).get(symbol, []))
                db.executemany("INSERT INTO rows VALUES (?,?,?)",
                               [(r["id"], r["date"], json.dumps(r, separators=(",", ":")))
                                for r in rows if r["date"] >= start])
            db.execute("INSERT INTO batches VALUES (?,?)", (offset, digest))
        print("PREPARED", offset + len(split["requested_symbols"]), flush=True)
    dates = [r[0] for r in db.execute("SELECT DISTINCT day FROM rows ORDER BY day")]
    target = output / "rows.jsonl.gz"
    row_count = 0
    with gzip.open(target.with_suffix(".tmp"), "wt") as stream:
        for day in dates:
            rows = [json.loads(r[0]) for r in db.execute("SELECT doc FROM rows WHERE day=? ORDER BY id", (day,))]
            rank_session(rows, aliases=aliases)
            for row in rows:
                stream.write(json.dumps(row, separators=(",", ":")) + "\n")
            row_count += len(rows)
    target.with_suffix(".tmp").replace(target)
    metadata = {"build_id": hashlib.sha256(target.read_bytes()).hexdigest(),
                "source_settings": settings, "rows": row_count, "dates": dates,
                "limitations": manifest["limitations"] + [
                    "Includes ETFs and other provider-listed equity instruments; no undated security-type filter.",
                    "Graph relationship confirmation must remain off for this price-only dataset.",
                    "Matching same-CUSIP alias observations count once among eligible rows; distinct predecessors remain."
                    if aliases else "No observation-level alias rank deduplication.",
                    "Split-adjusted technical ratios use provider correction history; nominal price uses raw IEX bars.",
                    "Dollar volume uses raw SIP sessions; missing SIP session blocks eligibility, price history retained."
                    if liquidity_archive is not None else "Dollar volume uses raw IEX sessions."]}
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2))
    db.close()
    print("FEATURE PREPARATION COMPLETE", row_count, flush=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--liquidity-archive", type=Path)
    parser.add_argument("--identity-events", type=Path)
    parser.add_argument("--history-resets", type=Path)
    parser.add_argument("--start", default="2021-10-01")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.dataset):
        parser.error("dataset names allow only letters, digits, underscore and hyphen")
    if not args.publish:
        return 0 if prepare(args.archive, args.output, args.dataset, args.start,
                            args.liquidity_archive, args.identity_events, args.history_resets) else 2
    metadata = json.loads((args.output / "metadata.json").read_text())
    path = args.output / "rows.jsonl.gz"
    if hashlib.sha256(path.read_bytes()).hexdigest() != metadata["build_id"]:
        raise ValueError("prepared rows changed")
    if metadata["source_settings"]["dataset"] != args.dataset:
        raise ValueError("wrong dataset")
    from db import store
    def batches():
        with gzip.open(path, "rt") as stream:
            batch = []
            for line in stream:
                batch.append(json.loads(line))
                if len(batch) == 2000:
                    yield batch
                    batch = []
            if batch:
                yield batch
    result = publish_rows(store, args.dataset, batches(), metadata)
    print("PUBLISHED", args.dataset, result["rows"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
