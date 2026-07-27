from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from point_in_time_data import (
    DatasetManifest,
    PointInTimeContext,
    PointInTimeDataError,
)
from strategies import graph_nexus_analysis as graph


UTC = timezone.utc


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _context(as_of: str) -> PointInTimeContext:
    return PointInTimeContext(
        as_of=_ts(as_of),
        manifest=DatasetManifest(
            manifest_id="daily-overlay-manifest",
            source_hashes={"daily_overlay": "sha256:daily"},
            created_at=_ts("2026-03-03T00:00:00Z"),
        ),
    )


def _session_close(session_date: date) -> datetime:
    return datetime.combine(session_date, time(21, 0), tzinfo=UTC)


def test_same_session_daily_close_is_hidden_before_exchange_close():
    bars = [
        {"t": "2026-02-27T05:00:00Z", "c": 99.0},
        {"t": "2026-03-02T05:00:00Z", "c": 101.0},
    ]

    closes = graph._point_in_time_closes(
        bars,
        "2026-03-02",
        context=_context("2026-03-02T20:59:59Z"),
        session_close_resolver=_session_close,
    )

    assert closes == [99.0]


def test_same_session_daily_close_is_visible_at_exchange_close():
    bars = [{"t": "2026-03-02T05:00:00Z", "c": 101.0}]

    closes = graph._point_in_time_closes(
        bars,
        "2026-03-02",
        context=_context("2026-03-02T21:00:00Z"),
        session_close_resolver=_session_close,
    )

    assert closes == [101.0]


def test_intraday_prefetch_cannot_expose_a_partial_same_day_daily_close():
    bars = [
        {"t": "2026-02-27T20:00:00Z", "c": 99.0},
        {"t": "2026-03-02T14:00:00Z", "c": 100.0},
        {"t": "2026-03-02T20:00:00Z", "c": 101.0},
    ]

    closes = graph._daily_closes_from_intraday(
        bars,
        "2026-03-02",
        context=_context("2026-03-02T20:30:00Z"),
        session_close_resolver=_session_close,
    )

    assert closes == [99.0]


def test_strict_daily_overlay_requires_a_session_close_resolver():
    with pytest.raises(PointInTimeDataError, match="session close"):
        graph._point_in_time_closes(
            [{"t": "2026-03-02T05:00:00Z", "c": 101.0}],
            "2026-03-02",
            context=_context("2026-03-02T20:00:00Z"),
        )


def test_explicit_live_context_preserves_current_overlay_behavior():
    context = PointInTimeContext.for_live(
        as_of=_ts("2026-03-02T20:00:00Z"),
        manifest=_context("2026-03-02T20:00:00Z").manifest,
    )
    first_day = date(2026, 2, 2)
    bars = [
        {
            "t": f"{(first_day + timedelta(days=index)).isoformat()}T21:00:00Z",
            "c": 100.0 + index,
        }
        for index in range(29)
    ]
    cache = {"_overlay_bars_raw": {"SPY": bars}}

    regime = graph._detect_market_regime(
        cache,
        {},
        "2026-03-02",
        context=context,
    )

    assert regime == "bull"


def test_intraday_daily_close_sorts_shuffled_regular_session_bars():
    bars = [
        {"t": "2026-03-02T20:00:00Z", "c": 103.0},
        {"t": "2026-03-02T15:00:00Z", "c": 101.0},
        {"t": "2026-02-27T20:00:00Z", "c": 99.0},
        {"t": "2026-03-02T19:00:00Z", "c": 102.0},
    ]

    closes = graph._daily_closes_from_intraday(
        bars,
        "2026-03-02",
        context=_context("2026-03-02T21:00:00Z"),
        session_close_resolver=_session_close,
    )

    assert closes == [99.0, 103.0]


def test_intraday_daily_close_excludes_pre_market_and_after_hours_bars():
    bars = [
        {"t": "2026-03-02T22:00:00Z", "c": 999.0},
        {"t": "2026-03-02T20:00:00Z", "c": 103.0},
        {"t": "2026-03-02T13:00:00Z", "c": 888.0},
        {"t": "2026-03-02T19:00:00Z", "c": 102.0},
    ]

    closes = graph._daily_closes_from_intraday(
        bars,
        "2026-03-02",
        context=_context("2026-03-02T21:00:00Z"),
        session_close_resolver=_session_close,
    )

    assert closes == [103.0]
