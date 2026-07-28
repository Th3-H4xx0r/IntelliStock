"""Read-only Neo4j query recording and deterministic replay.

The recorder is used only while building forward point-in-time evidence.  It
wraps the normal Neo4j driver, materializes every read result, and exports a
JSON-safe ledger.  Historical runs receive :class:`ReplayGraphDriver`; any
query or occurrence not present in the ledger fails closed instead of reaching
the current graph.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
import threading
from typing import Any, Mapping

from point_in_time_data import PointInTimeDataError


_WRITE_CYPHER = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP)\b",
    flags=re.IGNORECASE,
)


def _canonical_payload(value: Any) -> Any:
    # Import lazily so point_in_time_registry can decode graph ledgers without
    # creating an import cycle at module load.
    from point_in_time_registry import canonical_payload

    return canonical_payload(value)


def _normalize_query(query: Any) -> str:
    text = " ".join(str(query or "").split())
    if not text:
        raise PointInTimeDataError("graph snapshot query is empty")
    if _WRITE_CYPHER.search(text):
        raise PointInTimeDataError(
            "point-in-time graph capture is read-only"
        )
    return text


def _query_identity(query: Any, parameters: Mapping[str, Any] | None) -> tuple:
    normalized_query = _normalize_query(query)
    canonical_parameters = _canonical_payload(dict(parameters or {}))
    encoded = json.dumps(
        [normalized_query, canonical_parameters],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return (
        hashlib.sha256(encoded).hexdigest(),
        normalized_query,
        canonical_parameters,
    )


def _record_mapping(record: Any) -> dict[str, Any]:
    if isinstance(record, Mapping):
        raw = dict(record)
    else:
        try:
            raw = dict(record)
        except Exception as exc:
            data = getattr(record, "data", None)
            if not callable(data):
                raise PointInTimeDataError(
                    "graph query returned a non-record value"
                ) from exc
            raw = data()
            if not isinstance(raw, Mapping):
                raise PointInTimeDataError(
                    "graph query record data must be a mapping"
                )
    canonical = _canonical_payload(raw)
    if not isinstance(canonical, dict):
        raise PointInTimeDataError(
            "graph query record could not be canonicalized"
        )
    return canonical


@dataclass(frozen=True, slots=True)
class _ReplaySummary:
    counters: dict
    result_available_after: int = 0
    result_consumed_after: int = 0


class _ReplayResult:
    """Small Neo4j-result-compatible materialized result."""

    __slots__ = ("_rows",)

    def __init__(self, rows):
        canonical = _canonical_payload(list(rows or ()))
        if not isinstance(canonical, list):
            raise PointInTimeDataError("graph result rows must be a list")
        self._rows = canonical

    def __iter__(self):
        return iter(deepcopy(self._rows))

    def data(self):
        return deepcopy(self._rows)

    def single(self, strict: bool = False):
        if strict and len(self._rows) != 1:
            raise PointInTimeDataError(
                "strict graph result did not contain exactly one record"
            )
        return deepcopy(self._rows[0]) if self._rows else None

    def consume(self):
        return _ReplaySummary(counters={})

    def peek(self):
        return deepcopy(self._rows[0]) if self._rows else None


class _RecordingSession:
    __slots__ = ("_owner", "_delegate", "_entered")

    def __init__(self, owner, delegate):
        self._owner = owner
        self._delegate = delegate
        self._entered = False

    def __enter__(self):
        enter = getattr(self._delegate, "__enter__", None)
        if callable(enter):
            entered = enter()
            if entered is not None:
                self._delegate = entered
        self._entered = True
        return self

    def __exit__(self, exc_type, exc, tb):
        exit_method = getattr(self._delegate, "__exit__", None)
        if callable(exit_method):
            return bool(exit_method(exc_type, exc, tb))
        close = getattr(self._delegate, "close", None)
        if callable(close):
            close()
        return False

    def close(self):
        close = getattr(self._delegate, "close", None)
        if callable(close):
            return close()
        return None

    def run(self, query, parameters=None, **kwargs):
        params = dict(parameters or {})
        params.update(kwargs)
        identity, normalized_query, canonical_parameters = _query_identity(
            query, params
        )
        raw_result = self._delegate.run(query, params)
        rows = [_record_mapping(row) for row in raw_result]
        self._owner._record(
            identity,
            normalized_query,
            canonical_parameters,
            rows,
        )
        return _ReplayResult(rows)


class RecordingGraphDriver:
    """Neo4j driver wrapper that records every read query and result."""

    __slots__ = ("_delegate", "_queries", "_lock")

    def __init__(self, delegate):
        if delegate is None or not callable(getattr(delegate, "session", None)):
            raise PointInTimeDataError(
                "recording graph driver requires a Neo4j-compatible delegate"
            )
        self._delegate = delegate
        self._queries: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def session(self, **kwargs):
        return _RecordingSession(self, self._delegate.session(**kwargs))

    def verify_connectivity(self):
        verify = getattr(self._delegate, "verify_connectivity", None)
        return verify() if callable(verify) else None

    def close(self):
        close = getattr(self._delegate, "close", None)
        return close() if callable(close) else None

    def _record(self, identity, query, parameters, rows):
        with self._lock:
            entry = self._queries.get(identity)
            if entry is None:
                entry = {
                    "query": query,
                    "parameters": deepcopy(parameters),
                    "occurrences": [],
                }
                self._queries[identity] = entry
            elif (
                entry["query"] != query
                or entry["parameters"] != parameters
            ):
                raise PointInTimeDataError(
                    "graph query identity collision"
                )
            entry["occurrences"].append(deepcopy(rows))

    def export(self) -> dict[str, Any]:
        with self._lock:
            return {
                "recording_version": 1,
                "queries": deepcopy(dict(sorted(self._queries.items()))),
            }


class _ReplaySession:
    __slots__ = ("_owner",)

    def __init__(self, owner):
        self._owner = owner

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def close(self):
        return None

    def run(self, query, parameters=None, **kwargs):
        params = dict(parameters or {})
        params.update(kwargs)
        identity, normalized_query, canonical_parameters = _query_identity(
            query, params
        )
        rows = self._owner._next_occurrence(
            identity,
            normalized_query,
            canonical_parameters,
        )
        return _ReplayResult(rows)


class ReplayGraphDriver:
    """Read-only driver backed solely by an immutable recorded ledger."""

    __slots__ = ("_queries", "_positions", "_lock")

    def __init__(self, payload):
        canonical = _canonical_payload(payload)
        if (
            not isinstance(canonical, dict)
            or canonical.get("recording_version") != 1
            or not isinstance(canonical.get("queries"), dict)
        ):
            raise PointInTimeDataError(
                "graph snapshot recording is invalid"
            )
        self._queries = canonical["queries"]
        self._positions: dict[str, int] = {}
        self._lock = threading.RLock()

    def session(self, **_kwargs):
        return _ReplaySession(self)

    def verify_connectivity(self):
        return None

    def close(self):
        return None

    def _next_occurrence(self, identity, query, parameters):
        with self._lock:
            entry = self._queries.get(identity)
            if not isinstance(entry, dict):
                raise PointInTimeDataError(
                    "graph snapshot has no recorded query"
                )
            if (
                entry.get("query") != query
                or entry.get("parameters") != parameters
            ):
                raise PointInTimeDataError(
                    "graph snapshot query identity mismatch"
                )
            occurrences = entry.get("occurrences")
            if not isinstance(occurrences, list):
                raise PointInTimeDataError(
                    "graph snapshot occurrences are invalid"
                )
            position = self._positions.get(identity, 0)
            if position >= len(occurrences):
                raise PointInTimeDataError(
                    "graph snapshot query occurrences exhausted"
                )
            self._positions[identity] = position + 1
            return deepcopy(occurrences[position])


__all__ = [
    "RecordingGraphDriver",
    "ReplayGraphDriver",
]
