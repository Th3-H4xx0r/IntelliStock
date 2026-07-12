"""Parity tests for bar_time.bar_time_to_datetime: must return the SAME naive-UTC
datetimes as the previous dateutil-based parse, now via fromisoformat first
(≈50× faster on Alpaca's ISO timestamps) with a dateutil fallback."""

import datetime

from bar_time import bar_time_to_datetime as btd

_EXPECT = datetime.datetime(2026, 7, 12, 13, 45, 0)


def test_iso_z():
    assert btd("2026-07-12T13:45:00Z") == _EXPECT


def test_iso_offset_utc():
    assert btd("2026-07-12T13:45:00+00:00") == _EXPECT


def test_iso_offset_nonzero_converted_to_utc():
    assert btd("2026-07-12T06:45:00-07:00") == _EXPECT


def test_iso_naive_treated_as_utc():
    assert btd("2026-07-12T13:45:00") == _EXPECT


def test_microseconds_preserved():
    assert btd("2026-07-12T13:45:00.123456Z") == _EXPECT.replace(microsecond=123456)


def test_datetime_passthrough_is_naive_utc():
    aware = datetime.datetime(2026, 7, 12, 6, 45, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=-7)))
    assert btd(aware) == _EXPECT


def test_none_and_garbage():
    assert btd(None) is None
    assert btd("not-a-date") is None
