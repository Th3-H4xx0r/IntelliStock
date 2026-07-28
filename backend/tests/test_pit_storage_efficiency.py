"""Strict PIT snapshots must not become the next database elephant.

Four datasets (graph, fundamentals, universe, news) get frozen on every
capture. RethinkDB on this deployment is already swap-thrashing with
PriceHistory at ~2.3M rows, so an unbounded per-tick snapshot table would be a
real operational problem, not a theoretical one.

Three properties keep it bounded, and all three matter:

1. Snapshots are CONTENT-ADDRESSED (`id = sha256(dataset, payload)`), so an
   unchanged dataset is stored once no matter how often it is captured. The
   ticker universe barely moves and collapses to a single row for weeks.
2. Capture runs once per TRADING DAY, not once per tick. Bundle resolution is
   at-or-before `as_of` and the backtest lookback steps daily, so a daily
   bundle replays identically while storing ~7x less on an hourly instance.
3. Large payloads are stored zlib-compressed, with content identity still
   taken over the canonical UNCOMPRESSED form — compression must never change
   a hash, break dedupe, or make an existing sealed fixture unreplayable.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.graph_nexus_analysis as g  # noqa: E402
from point_in_time_registry import (  # noqa: E402
    _snapshot_hash,
    _snapshot_rows_match,
    decode_snapshot_payload,
    encode_snapshot_payload,
)

UTC = timezone.utc


# ------------------------------------------------------------- compression
def _big_payload():
    return {"edges": [{"src": f"AAPL{i}", "dst": f"MSFT{i}", "w": i * 0.001}
                      for i in range(400)]}


def test_large_payloads_are_compressed():
    encoded = encode_snapshot_payload(_big_payload())
    assert "payload_z" in encoded and "payload" not in encoded
    stored = len(encoded["payload_z"])
    assert stored < encoded["payload_bytes"] / 2, (
        f"expected real compression, got {stored} from {encoded['payload_bytes']}")


def test_small_payloads_are_left_alone():
    """Framing overhead would dominate a short ticker list."""
    encoded = encode_snapshot_payload({"tickers": ["AAPL", "MSFT"]})
    assert "payload" in encoded and "payload_z" not in encoded


def test_compression_round_trips_exactly():
    for payload in (_big_payload(), {"tickers": ["AAPL"]}, {}, {"n": [1, 2, 3]}):
        assert decode_snapshot_payload(encode_snapshot_payload(payload)) == payload


def test_legacy_plaintext_rows_stay_readable():
    """An old sealed fixture must not become unreplayable because the storage
    encoding changed."""
    assert decode_snapshot_payload({"payload": {"a": 1}}) == {"a": 1}


def test_compression_does_not_change_content_identity():
    """The hash is what dedupe and every sealed fixture depend on."""
    payload = _big_payload()
    before = _snapshot_hash("graph", payload)
    after = _snapshot_hash("graph", decode_snapshot_payload(
        encode_snapshot_payload(payload)))
    assert before == after


def test_rows_match_across_encodings():
    """A legacy plaintext row and a compressed row with identical content are
    the same immutable row, not a divergence."""
    payload = _big_payload()
    h = _snapshot_hash("graph", payload)
    base = {"id": h, "content_hash": h, "dataset": "graph", "record_version": 1}
    legacy = dict(base, payload=payload)
    packed = dict(base, **encode_snapshot_payload(payload))
    assert _snapshot_rows_match(legacy, packed)


def test_rows_with_different_content_still_diverge():
    a, b = _big_payload(), {"edges": []}
    ha = _snapshot_hash("graph", a)
    base = {"id": ha, "content_hash": ha, "dataset": "graph", "record_version": 1}
    assert not _snapshot_rows_match(dict(base, payload=a), dict(base, payload=b))


# ------------------------------------------------------------------ cadence
def test_capture_is_once_per_trading_day_by_default():
    cache = {}
    day = datetime(2026, 7, 29, 14, 30, tzinfo=UTC)
    assert g._pit_capture_due(cache, day, {}) is True
    for hour in range(1, 7):
        assert g._pit_capture_due(cache, day + timedelta(hours=hour), {}) is False
    assert g._pit_capture_due(cache, day + timedelta(days=1), {}) is True


def test_tick_interval_restores_per_tick_capture():
    cache = {}
    day = datetime(2026, 7, 29, 14, 30, tzinfo=UTC)
    cfg = {"pit_capture_interval": "tick"}
    assert g._pit_capture_due(cache, day, cfg) is True
    assert g._pit_capture_due(cache, day + timedelta(hours=1), cfg) is True


def test_cadence_fails_open_without_a_cache_or_timestamp():
    """A missing cache must not silently suppress capture — losing evidence is
    worse than storing a duplicate the content hash would dedupe anyway."""
    assert g._pit_capture_due(None, datetime(2026, 7, 29, tzinfo=UTC), {}) is True
    assert g._pit_capture_due({}, None, {}) is True


def test_daily_cadence_cuts_an_hourly_instance_by_about_seven():
    """The concrete win: one bundle per session instead of one per tick."""
    cache = {}
    start = datetime(2026, 7, 29, 13, 30, tzinfo=UTC)
    ticks = [start + timedelta(days=d, hours=h)
             for d in range(5) for h in range(7)]   # 7 ticks within each session
    captures = sum(1 for tick in ticks if g._pit_capture_due(cache, tick, {}))
    assert len(ticks) == 35
    assert captures == 5, f"expected one per day, got {captures}"
