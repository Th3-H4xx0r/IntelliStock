"""Task 3: timestamped market-mark contract.

Entry cost, fill price, and market mark are different concepts. These tests pin
the precedence, freshness, purpose-policy, and thread-safety contract that the
adapters and broker consume in Task 4.
"""
import os
import sys
import threading
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_marks import (
    MarkPurpose,
    MarkQuality,
    MarkSource,
    MarketMark,
    MarketMarkBook,
    classify_session,
    evaluate_mark,
)

NOW = datetime(2026, 7, 10, 14, 0, tzinfo=timezone.utc)


def make_mark(symbol="MRNA", price=76.51, source=MarkSource.BROKER_POSITION,
              quality=MarkQuality.BROKER_DERIVED, observed_at=NOW, received_at=None,
              bid=None, ask=None, bid_size=None, ask_size=None, feed="broker",
              session="regular", conditions=(), halted=False):
    return MarketMark(
        symbol=symbol, price=price, bid=bid, ask=ask, bid_size=bid_size,
        ask_size=ask_size, observed_at=observed_at,
        received_at=received_at or observed_at, source=source, feed=feed,
        quality=quality, session=session, conditions=conditions, halted=halted,
    )


def quote(price=100.0, spread=0.02, observed_at=NOW, received_at=None, size=5,
          halted=False, conditions=(), symbol="MRNA"):
    return make_mark(
        symbol=symbol, price=price, bid=price - spread / 2, ask=price + spread / 2,
        bid_size=size, ask_size=size, source=MarkSource.STREAM_QUOTE,
        quality=MarkQuality.SINGLE_EXCHANGE, feed="iex", observed_at=observed_at,
        received_at=received_at, conditions=conditions, halted=halted,
    )


def test_market_mark_book_rejects_older_and_expires_at_sla():
    book = MarketMarkBook()
    current = make_mark(observed_at=NOW)
    assert book.update(current) is True
    older = make_mark(price=81.36, source=MarkSource.FILL, feed="execution",
                      quality=MarkQuality.EXECUTION_ONLY,
                      observed_at=NOW - timedelta(minutes=5), received_at=NOW)
    assert book.update(older) is False
    assert book.fresh_price("MRNA", NOW + timedelta(seconds=59), 60) == 76.51
    assert book.fresh_price("MRNA", NOW + timedelta(seconds=61), 60) is None


def test_broker_position_replaces_fill_at_same_timestamp_but_not_quotes():
    book = MarketMarkBook()
    fill = make_mark(price=81.36, source=MarkSource.FILL,
                     quality=MarkQuality.EXECUTION_ONLY, feed="execution")
    assert book.update(fill) is True
    broker = make_mark(price=76.51)
    assert book.update(broker) is True
    assert book.get("MRNA").price == 76.51
    same_ts_fill = make_mark(price=99.0, source=MarkSource.FILL,
                             quality=MarkQuality.EXECUTION_ONLY, feed="execution")
    assert book.update(same_ts_fill) is False
    q = quote(price=77.0)
    assert book.update(q) is True
    same_ts_broker = make_mark(price=70.0)
    assert book.update(same_ts_broker) is False
    assert book.get("MRNA").price == 77.0


def test_symbols_are_case_normalized():
    book = MarketMarkBook()
    assert book.update(make_mark(symbol="mrna")) is True
    assert book.get("MRNA").symbol == "MRNA"
    assert book.fresh_price("mrna", NOW, 60) == 76.51


def test_snapshot_returns_defensive_copy():
    book = MarketMarkBook()
    book.update(make_mark())
    snap = book.snapshot()
    snap.pop("MRNA")
    assert book.get("MRNA") is not None


def test_invalid_prices_and_naive_timestamps_rejected():
    with pytest.raises(ValueError):
        make_mark(price=0.0)
    with pytest.raises(ValueError):
        make_mark(price=float("nan"))
    with pytest.raises(ValueError):
        make_mark(price=-5.0)
    with pytest.raises(ValueError):
        make_mark(observed_at=datetime(2026, 7, 10, 14, 0))  # naive


def test_fresh_price_filters_on_quality():
    book = MarketMarkBook()
    book.update(make_mark())  # BROKER_DERIVED
    assert book.fresh_price("MRNA", NOW, 60,
                            allowed_qualities={MarkQuality.CONSOLIDATED}) is None
    assert book.fresh_price("MRNA", NOW, 60,
                            allowed_qualities={MarkQuality.BROKER_DERIVED}) == 76.51


def test_age_seconds():
    assert make_mark().age_seconds(NOW + timedelta(seconds=42)) == 42.0


def test_concurrent_updates_are_thread_safe():
    book = MarketMarkBook()
    def writer(offset):
        for i in range(200):
            book.update(quote(price=100 + i,
                              observed_at=NOW + timedelta(seconds=i * 2 + offset)))
    threads = [threading.Thread(target=writer, args=(o,)) for o in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert book.get("MRNA").price == 100 + 199


# --- purpose policies -------------------------------------------------------

def test_decision_purpose_accepts_fresh_quote_and_enforces_60s_sla():
    ok = evaluate_mark(quote(), MarkPurpose.DECISION, NOW + timedelta(seconds=59))
    assert ok.allowed and not ok.degraded
    stale = evaluate_mark(quote(), MarkPurpose.DECISION, NOW + timedelta(seconds=61))
    assert not stale.allowed


def test_decision_purpose_broker_fallback_is_120s():
    broker = make_mark()
    assert evaluate_mark(broker, MarkPurpose.DECISION,
                         NOW + timedelta(seconds=119)).allowed
    assert not evaluate_mark(broker, MarkPurpose.DECISION,
                             NOW + timedelta(seconds=121)).allowed


def test_fill_mark_fails_every_exposure_increase_purpose():
    fill = make_mark(source=MarkSource.FILL, quality=MarkQuality.EXECUTION_ONLY,
                     feed="execution")
    for purpose in (MarkPurpose.DECISION, MarkPurpose.SUBMISSION):
        check = evaluate_mark(fill, purpose, NOW)
        assert not check.allowed
        assert any("fill" in reason or "source" in reason for reason in check.reasons)


def test_submission_purpose_is_tighter_than_decision():
    q = quote()
    assert evaluate_mark(q, MarkPurpose.SUBMISSION, NOW + timedelta(seconds=29)).allowed
    assert not evaluate_mark(q, MarkPurpose.SUBMISSION, NOW + timedelta(seconds=31)).allowed


def test_crossed_locked_and_zero_size_quotes_fail_increases():
    crossed = make_mark(bid=100.10, ask=100.00, bid_size=1, ask_size=1,
                        price=100.05, source=MarkSource.STREAM_QUOTE,
                        quality=MarkQuality.SINGLE_EXCHANGE, feed="iex")
    locked = make_mark(bid=100.0, ask=100.0, bid_size=1, ask_size=1,
                       price=100.0, source=MarkSource.STREAM_QUOTE,
                       quality=MarkQuality.SINGLE_EXCHANGE, feed="iex")
    empty = quote(size=0)
    for bad in (crossed, locked, empty):
        assert not evaluate_mark(bad, MarkPurpose.DECISION, NOW).allowed


def test_halt_and_luld_conditions_fail_increases_but_not_risk_reduction():
    halted = quote(halted=True)
    luld = quote(conditions=("LULD",))
    for bad in (halted, luld):
        assert not evaluate_mark(bad, MarkPurpose.DECISION, NOW).allowed
    check = evaluate_mark(halted, MarkPurpose.RISK_REDUCTION, NOW)
    assert check.allowed and check.degraded


def test_clock_skew_fails_increases():
    skewed = quote(received_at=NOW + timedelta(seconds=15))
    assert not evaluate_mark(skewed, MarkPurpose.DECISION, NOW + timedelta(seconds=15)).allowed


def test_risk_reduction_allows_degraded_broker_state_and_records_it():
    old_broker = make_mark()
    check = evaluate_mark(old_broker, MarkPurpose.RISK_REDUCTION,
                          NOW + timedelta(seconds=300))
    assert check.allowed
    assert check.degraded
    assert check.reasons  # degraded source/age must be recorded
    fresh = evaluate_mark(quote(), MarkPurpose.RISK_REDUCTION, NOW)
    assert fresh.allowed and not fresh.degraded


def test_session_classification():
    assert classify_session(datetime(2026, 7, 10, 14, 0, tzinfo=timezone.utc)) == "regular"  # 10:00 ET Fri
    assert classify_session(datetime(2026, 7, 10, 12, 30, tzinfo=timezone.utc)) == "pre"     # 8:30 ET
    assert classify_session(datetime(2026, 7, 10, 21, 30, tzinfo=timezone.utc)) == "after"   # 17:30 ET
    assert classify_session(datetime(2026, 7, 12, 15, 0, tzinfo=timezone.utc)) == "closed"   # Sunday
