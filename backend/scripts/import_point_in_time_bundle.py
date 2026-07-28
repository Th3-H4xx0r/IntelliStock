#!/usr/bin/env python3
"""Validate or import one immutable strict point-in-time dataset bundle.

Dry-run validation is the default. Persistence requires the explicit
``--apply`` flag. Output contains identities and hashes only, never payloads.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Mapping

from point_in_time_data import PointInTimeDataError, require_aware_utc
from point_in_time_registry import (
    InMemoryPointInTimeRegistry,
    REQUIRED_DATASETS,
    RethinkPointInTimeRegistry,
)


def _prepare(payload: Mapping) -> tuple[dict, object]:
    if not isinstance(payload, Mapping):
        raise PointInTimeDataError("PIT bundle document must be a mapping")
    allowed = {
        "as_of",
        "created_at",
        "code_revision",
        "datasets",
        "source_hashes",
        "manifest_id",
    }
    extra = sorted(set(payload) - allowed)
    if extra:
        raise PointInTimeDataError(
            "PIT bundle contains unsupported top-level fields"
        )
    datasets = payload.get("datasets")
    code_revision = str(payload.get("code_revision") or "").strip()
    if not code_revision:
        raise PointInTimeDataError("PIT bundle code_revision is required")
    kwargs = {
        "as_of": require_aware_utc(
            payload.get("as_of"),
            field="bundle as_of",
        ),
        "datasets": datasets,
        "code_revision": code_revision,
    }
    if payload.get("created_at") is not None:
        kwargs["created_at"] = require_aware_utc(
            payload.get("created_at"),
            field="bundle created_at",
        )
    record = InMemoryPointInTimeRegistry().finalize_bundle(**kwargs)
    declared_hashes = payload.get("source_hashes")
    if declared_hashes is not None:
        if not isinstance(declared_hashes, Mapping) or dict(declared_hashes) != dict(
            record.source_hashes
        ):
            raise PointInTimeDataError(
                "declared source hashes do not match bundle payloads"
            )
    declared_manifest = str(payload.get("manifest_id") or "").strip()
    if declared_manifest and declared_manifest != record.manifest_id:
        raise PointInTimeDataError(
            "declared manifest identity does not match bundle payloads"
        )
    return kwargs, record


def import_bundle_document(
    payload: Mapping,
    *,
    apply: bool = False,
    registry=None,
) -> dict:
    """Validate ``payload`` and optionally publish it manifest-last."""

    kwargs, validated = _prepare(payload)
    record = validated
    if apply:
        target = registry or RethinkPointInTimeRegistry()
        record = target.finalize_bundle(**kwargs)
        if record.manifest_id != validated.manifest_id:
            raise PointInTimeDataError(
                "persisted manifest identity differs from validated bundle"
            )
    return {
        "applied": bool(apply),
        "manifest_id": record.manifest_id,
        "as_of": record.as_of.isoformat().replace("+00:00", "Z"),
        "code_revision": record.code_revision,
        "datasets": list(REQUIRED_DATASETS),
        "source_hashes": dict(record.source_hashes),
        "provenance": record.provenance,
        "status": record.status,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, help="Strict PIT bundle JSON")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Publish snapshots and manifest; omitted means dry-run",
    )
    args = parser.parse_args(argv)
    try:
        try:
            from dotenv import load_dotenv

            load_dotenv(
                os.path.join(
                    os.path.dirname(__file__),
                    "..",
                    "..",
                    ".env",
                )
            )
        except Exception:
            pass
        payload = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
        result = import_bundle_document(payload, apply=args.apply)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "applied": False,
                    "error": type(exc).__name__,
                    "status": "rejected",
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
