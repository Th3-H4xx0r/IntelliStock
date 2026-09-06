#!/usr/bin/env python3
"""Normalize verified duplicate ticker aliases before feature preparation.

Dated provider name-change records establish identity. Remove an old ticker's
prefix only when every raw close and volume observation is preserved under its
successor. Never drop an issuer because its successor history is unavailable.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path


def duplicate_prefix_cutoffs(raw, events):
    incoming, outgoing = {}, {}
    for event in events:
        old, new, day = event["old_symbol"], event["new_symbol"], event["process_date"]
        if old != new:
            incoming[new] = max(incoming.get(new, ""), day)
            outgoing[old] = max(outgoing.get(old, ""), day)
    protected = {s for s, day in incoming.items() if day > outgoing.get(s, "")}
    keyed = {s: {b["t"][:10]: b for b in bars} for s, bars in raw.items()}
    cutoffs, unresolved, replacements = {}, [], {}
    for event in sorted(events, key=lambda e: e["process_date"]):
        old, new, day = event["old_symbol"], event["new_symbol"], event["process_date"]
        if old == new or old in protected:
            continue
        prefix = {d: b for d, b in keyed.get(old, {}).items()
                  if cutoffs.get(old, "") <= d < day}
        if not prefix:
            continue
        successor = keyed.get(new, {})
        equal = sum(d in successor and all(b[k] == successor[d][k] for k in ("c", "v"))
                    for d, b in prefix.items())
        if equal == len(prefix):
            replacements.setdefault(old, []).append((cutoffs.get(old, ""), day, new))
            cutoffs[old] = day
        else:
            unresolved.append({"old": old, "new": new, "date": day,
                               "prefix_bars": len(prefix), "preserved_equal_bars": equal})
    # Validate the final state, not merely the pairwise source matches: a
    # successor can itself be removed by another event (including a cycle).
    for old, cutoff in cutoffs.items():
        for day in keyed[old]:
            if day >= cutoff:
                continue
            pending, seen, preserved = [old], set(), False
            while pending:
                symbol = pending.pop()
                if symbol in seen:
                    continue
                seen.add(symbol)
                if day >= cutoffs.get(symbol, ""):
                    preserved = True
                    break
                pending.extend(new for lo, hi, new in replacements.get(symbol, [])
                               if lo <= day < hi)
            if not preserved:
                raise ValueError(f"{old} {day}: no retained successor observation")
    return cutoffs, unresolved


@lru_cache(maxsize=8)
def read_batch(path):
    with gzip.open(path, "rt") as stream:
        return json.load(stream)


def normalize(sources, events_path, destination):
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "manifest.json").exists():
        raise ValueError("normalized archive already exists")
    index = {}
    for source in sources:
        if (source / "manifest.json").exists():
            expected = json.loads((source / "manifest.json").read_text())["symbols"]
        else:
            expected = len(json.loads((source / "symbols.json").read_text()))
        count = 0
        for offset in range(0, expected, 100):
            raw_path, split_path = [source / f"{offset:05d}-{a}.json.gz" for a in ("raw", "split")]
            raw, split = read_batch(raw_path), read_batch(split_path)
            if raw["requested_symbols"] != split["requested_symbols"]:
                raise ValueError("source adjustment requests differ")
            for symbol in raw["requested_symbols"]:
                if symbol in index:
                    raise ValueError(f"duplicate source symbol {symbol}")
                index[symbol] = (raw_path, split_path)
            count += len(raw["requested_symbols"])
        if count != expected:
            raise ValueError("source archive incomplete")
    events = json.loads(events_path.read_text())
    wanted = {e[k] for e in events for k in ("old_symbol", "new_symbol")}
    raw = {s: read_batch(paths[0])["bars"].get(s, []) for s, paths in index.items() if s in wanted}
    cutoffs, unresolved = duplicate_prefix_cutoffs(raw, events)
    del raw
    symbols = list(index)
    for offset in range(0, len(symbols), 100):
        part = symbols[offset:offset + 100]
        for adjustment, field in (("raw", 0), ("split", 1)):
            bars = {}
            for symbol in part:
                source = read_batch(index[symbol][field])["bars"].get(symbol, [])
                kept = [b for b in source if b["t"][:10] >= cutoffs.get(symbol, "")]
                if kept:
                    bars[symbol] = kept
            path = destination / f"{offset:05d}-{adjustment}.json.gz"
            with gzip.open(path, "wt") as stream:
                json.dump({"requested_symbols": part, "bars": bars}, stream, separators=(",", ":"))
        if offset % 1000 == 0:
            print("NORMALIZED", offset + len(part), "/", len(symbols), flush=True)
    report = {"cutoffs": cutoffs, "unresolved": unresolved,
              "events_sha256": hashlib.sha256(events_path.read_bytes()).hexdigest(),
              "normalizer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (destination / "identity-audit.json").write_text(json.dumps(report, indent=2))
    manifest = json.loads((sources[0] / "manifest.json").read_text())
    manifest.update(symbols=len(symbols), sources=[str(s.resolve()) for s in sources],
                    identity_audit_sha256=hashlib.sha256((destination / "identity-audit.json").read_bytes()).hexdigest())
    manifest["limitations"] += [
        "Universe augmented by dated name-change symbols plus SIVB/SIVBQ coverage probes.",
        "Verified duplicated old ticker prefixes removed; successor retains every raw observation.",
        "Unresolved identity cases are retained and recorded; inspect any eligible or filled case."]
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("NORMALIZED ARCHIVE COMPLETE", len(symbols), "symbols;", len(cutoffs), "alias prefixes", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, action="append", required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    normalize(args.source, args.events, args.output)
