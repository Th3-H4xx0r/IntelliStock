"""Benzinga records must carry an availability timestamp.

Without one, `filter_available` refuses them, and in research mode that meant
EVERY Benzinga record was silently dropped: backtests ran with analyst ratings,
insider trades, congressional trades, M&A, IPOs, splits and the earnings
calendar blank while live ran with all of them. The backtest was not testing the
live strategy on that entire signal layer.

The stamp is deliberately conservative -- the close of the record's own date --
so a record dated D is first visible on D+1 and a decision during D cannot see
it. These tests pin both halves: that a stamp exists at all, and that it is
never early enough to leak.
"""

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from benzinga_client import _availability_stamp  # noqa: E402
from point_in_time_data import (  # noqa: E402
    DatasetManifest,
    PointInTimeContext,
    filter_available,
)


def _ctx(as_of):
    return PointInTimeContext(
        as_of=as_of,
        manifest=DatasetManifest(
            manifest_id="benzinga-availability-manifest",
            source_hashes={"benzinga": "sha256:benzinga"},
            created_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        ),
        strict=False,
        is_live=False,
    )


def test_stamp_is_after_the_us_close_on_the_records_own_date():
    # 21:00Z is 17:00 ET in EDT and 16:00 ET in EST -- after the close in both.
    assert _availability_stamp("2026-04-15") == "2026-04-15T21:00:00Z"


def test_stamp_rejects_anything_that_is_not_a_date():
    for junk in ("", None, "not-a-date", "2026-13", 42, "  "):
        assert _availability_stamp(junk) == ""


def test_stamp_tolerates_a_full_timestamp_and_keeps_the_date():
    assert _availability_stamp("2026-04-15T13:30:00Z") == "2026-04-15T21:00:00Z"


def test_record_is_invisible_during_its_own_session():
    """The leak this guards: a record dated D must not inform a decision on D."""
    rec = {"date": "2026-04-15", "available_at": _availability_stamp("2026-04-15")}
    intraday = datetime.datetime(2026, 4, 15, 13, 45, tzinfo=datetime.timezone.utc)
    assert filter_available(
        [rec], context=_ctx(intraday), available_at=lambda r: r.get("available_at")
    ) == ()


def test_record_is_visible_the_next_session():
    rec = {"date": "2026-04-15", "available_at": _availability_stamp("2026-04-15")}
    next_day = datetime.datetime(2026, 4, 16, 13, 45, tzinfo=datetime.timezone.utc)
    assert len(filter_available(
        [rec], context=_ctx(next_day), available_at=lambda r: r.get("available_at")
    )) == 1


def test_every_normalizer_emits_the_field():
    """Regression guard: a new endpoint must not silently reintroduce the drop."""
    import ast

    src = open(os.path.join(os.path.dirname(__file__), "..", "benzinga_client.py")).read()
    tree = ast.parse(src)
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        # A normalized record is any dict literal carrying both a ticker and a
        # date -- that shape is what reaches filter_available.
        if {"ticker", "date"} <= keys and "available_at" not in keys:
            missing.append(node.lineno)
    assert not missing, f"normalized records without available_at at lines {missing}"
