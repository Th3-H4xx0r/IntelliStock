#!/usr/bin/env python3
"""Report strict PIT coverage without reading or printing dataset payloads."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import os
from typing import Iterable, Mapping

from point_in_time_data import PointInTimeDataError, require_aware_utc
from point_in_time_registry import MANIFEST_TABLE, REQUIRED_DATASETS


def _manifest_cutoff(row: Mapping) -> datetime:
    return require_aware_utc(row.get("as_of"), field="manifest as_of")


def summarize_coverage(
    manifest_rows: Iterable[Mapping],
    *,
    legacy_row_count: int,
    start_date: date | None = None,
    end_date: date | None = None,
    expected_sessions: Iterable[date] = (),
) -> dict:
    """Build a payload-free strict coverage report from manifest metadata."""

    complete: list[datetime] = []
    incomplete: list[dict] = []
    required = set(REQUIRED_DATASETS)
    for row in manifest_rows or ():
        if not isinstance(row, Mapping):
            continue
        if (
            str(row.get("status") or "") != "finalized"
            or str(row.get("provenance") or "") != "strict_verified"
        ):
            continue
        cutoff = _manifest_cutoff(row)
        hashes = row.get("source_hashes")
        hashes = hashes if isinstance(hashes, Mapping) else {}
        missing = sorted(
            name
            for name in required
            if not str(hashes.get(name) or "").startswith("sha256:")
        )
        if missing:
            incomplete.append(
                {
                    "as_of": cutoff.isoformat().replace("+00:00", "Z"),
                    "missing_datasets": missing,
                }
            )
        else:
            complete.append(cutoff)

    complete.sort()
    finalized_dates = sorted({cutoff.date().isoformat() for cutoff in complete})
    expected = sorted({session.isoformat() for session in expected_sessions})
    available = set(finalized_dates)
    return {
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "finalized_dates": finalized_dates,
        "finalized_manifest_count": len(complete),
        "earliest_cutoff": (
            complete[0].isoformat().replace("+00:00", "Z")
            if complete
            else None
        ),
        "latest_cutoff": (
            complete[-1].isoformat().replace("+00:00", "Z")
            if complete
            else None
        ),
        "verified_month_count": len(
            {(cutoff.year, cutoff.month) for cutoff in complete}
        ),
        "missing_sessions": [
            session for session in expected if session not in available
        ],
        "incomplete_manifests": sorted(
            incomplete,
            key=lambda item: item["as_of"],
        ),
        "legacy_row_count": max(0, int(legacy_row_count)),
    }


def authoritative_nyse_sessions(start_date: date, end_date: date) -> tuple[date, ...]:
    if start_date > end_date:
        raise PointInTimeDataError("coverage start date is after end date")
    try:
        import exchange_calendars
        import pandas
    except Exception as exc:
        raise PointInTimeDataError(
            "authoritative XNYS calendar is unavailable"
        ) from exc
    calendar = exchange_calendars.get_calendar("XNYS")
    sessions = calendar.sessions_in_range(
        pandas.Timestamp(start_date),
        pandas.Timestamp(end_date),
    )
    return tuple(session.date() for session in sessions)


def _read_metadata() -> tuple[list[dict], int]:
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    except Exception:
        pass
    from rethinkdb import RethinkDB

    r = RethinkDB()
    conn = r.connect(
        host=os.environ.get("RETHINKDB_HOST", "localhost"),
        port=int(os.environ.get("RETHINKDB_PORT", "28015")),
        timeout=20,
    )
    try:
        db_name = os.environ.get("RETHINKDB_DB", "IntelliStock")
        tables = set(r.db(db_name).table_list().run(conn))
        manifests = []
        if MANIFEST_TABLE in tables:
            manifests = list(
                r.db(db_name)
                .table(MANIFEST_TABLE)
                .pluck(
                    "as_of",
                    "status",
                    "provenance",
                    "source_hashes",
                )
                .run(conn)
            )
        legacy_count = 0
        if "GraphNexusTradeContexts" in tables:
            legacy_count = int(
                r.db(db_name)
                .table("GraphNexusTradeContexts")
                .filter(
                    lambda row: (
                        row["pit_provenance"].default("")
                        != "strict_verified"
                    )
                    | (row["pit_manifest_id"].default("") == "")
                    | (row["pit_as_of"].default("") == "")
                )
                .count()
                .run(conn)
            )
        return manifests, legacy_count
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=_date_arg)
    parser.add_argument("--end", type=_date_arg)
    args = parser.parse_args(argv)
    if (args.start is None) != (args.end is None):
        parser.error("--start and --end must be supplied together")
    try:
        sessions = (
            authoritative_nyse_sessions(args.start, args.end)
            if args.start is not None
            else ()
        )
        manifests, legacy_count = _read_metadata()
        report = summarize_coverage(
            manifests,
            legacy_row_count=legacy_count,
            start_date=args.start,
            end_date=args.end,
            expected_sessions=sessions,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"error": type(exc).__name__, "status": "rejected"},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(report, sort_keys=True))
    return 1 if report["missing_sessions"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
