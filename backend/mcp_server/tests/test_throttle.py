"""Login throttling: per-username lockouts with doubling backoff, the global burst lockout, expiry, purge."""
import pytest

from conftest import FakeClock
from mcp_server.authdb import AuthDB
from mcp_server.throttle import LoginThrottle, normalize_username

START = 1_800_000_000.0
DAY = 86_400


@pytest.fixture
def clock():
    return FakeClock(START)


@pytest.fixture
def db(settings):
    store = AuthDB(settings.db_path)
    yield store
    store.close()


@pytest.fixture
def lockouts():
    """Every (scope, username, seconds) the throttle reported, in order."""
    return []


def _throttle(db, clock, lockouts, **kwargs):
    return LoginThrottle(db, clock=clock, on_lockout=lambda *event: lockouts.append(event), **kwargs)


@pytest.fixture
def throttle(db, clock, lockouts):
    return _throttle(db, clock, lockouts)


def _fail(throttle, username, times, kind="password"):
    """record_failure `times` times; what each call returned."""
    return [throttle.record_failure(username, kind) for _ in range(times)]


def _rows(db, table):
    return db.query_one(f"SELECT COUNT(*) AS n FROM {table}")["n"]


def test_five_failures_lock_user_for_900s(throttle, clock, lockouts):
    # Every kind of failed step counts toward the same five.
    for kind in ("password", "password", "passkey", "totp"):
        assert throttle.record_failure("operator", kind) is None
    assert throttle.check("operator") is None
    assert lockouts == []
    assert throttle.record_failure("operator", "password") == 900
    assert lockouts == [("user", "operator", 900)]
    assert throttle.check("operator") == 900
    assert throttle.check("someone-else") is None  # a user lockout locks that user only
    clock.advance(899.5)
    assert throttle.check("operator") == 1  # rounded up: never 0 while still locked
    clock.advance(0.5)
    assert throttle.check("operator") is None


def test_lockout_escalates_doubling_capped_86400(throttle, clock, lockouts):
    expected = [900, 1800, 3600, 7200, 14400, 28800, 57600, 86400, 86400]
    for seconds in expected:
        assert _fail(throttle, "operator", 5) == [None, None, None, None, seconds]
        assert throttle.check("operator") == seconds
        clock.advance(seconds)  # wait the lockout out, then fail again at once
        assert throttle.check("operator") is None
    # The 9th lockout is set exactly 24 h after the 8th: still remembered, so it stays capped.
    assert lockouts == [("user", "operator", seconds) for seconds in expected]


def test_level_resets_after_24h_without_a_lockout(throttle, clock):
    assert _fail(throttle, "operator", 5)[-1] == 900
    clock.advance(900)
    assert _fail(throttle, "operator", 5)[-1] == 1800
    clock.advance(DAY + 1)  # more than 24 h since the last lockout was set
    assert _fail(throttle, "operator", 5)[-1] == 900


def test_failures_before_a_lockout_do_not_count_again(db, clock, lockouts):
    # A window longer than the lockout, so the five that set it are still inside it afterwards.
    throttle = _throttle(db, clock, lockouts, window_s=3600)
    assert _fail(throttle, "operator", 5)[-1] == 900
    clock.advance(900)
    assert _fail(throttle, "operator", 4) == [None] * 4  # the user gets a fresh five, not one
    assert throttle.record_failure("operator", "password") == 1800
    assert lockouts == [("user", "operator", 900), ("user", "operator", 1800)]


def test_global_burst_locks_everyone_and_fires_callback(throttle, clock, lockouts):
    for n in range(19):
        assert throttle.record_failure(f"guess-{n}", "password") is None
        clock.advance(30)  # 20 failures across 570 s: inside the 600 s window
    assert throttle.check("anyone") is None
    assert throttle.record_failure("guess-19", "password") == 600
    assert lockouts == [("global", None, 600)]
    assert throttle.check("anyone") == 600
    assert throttle.check("operator") == 600
    clock.advance(600)
    assert throttle.check("anyone") is None
    # The global lockout does not escalate: the next burst locks for 600 s again.
    assert _fail(throttle, "guess-20", 1) == [None]
    for n in range(21, 40):
        throttle.record_failure(f"guess-{n}", "password")
    assert lockouts == [("global", None, 600), ("global", None, 600)]


def test_slow_failures_do_not_trip_the_global_lockout(throttle, clock, lockouts):
    for n in range(20):
        assert throttle.record_failure(f"guess-{n}", "password") is None
        clock.advance(32)  # 20 failures across 608 s: never 20 inside one 600 s window
    assert throttle.check("anyone") is None
    assert lockouts == []


def test_one_failure_can_set_both_lockouts(throttle, clock, lockouts):
    for n in range(15):
        throttle.record_failure(f"guess-{n}", "password")
    assert _fail(throttle, "operator", 4) == [None] * 4
    assert throttle.record_failure("operator", "password") == 900  # the 20th overall and the user's 5th
    assert lockouts == [("user", "operator", 900), ("global", None, 600)]
    assert throttle.check("operator") == 900  # the longer of the two
    assert throttle.check("anyone") == 600


def test_success_clears_failures(throttle, clock, lockouts):
    _fail(throttle, "operator", 4)
    _fail(throttle, "other", 4)
    throttle.record_success(" Operator ")
    assert _fail(throttle, "operator", 4) == [None] * 4  # the count started over
    assert throttle.record_failure("other", "password") == 900  # another user's count is untouched
    assert throttle.record_failure("operator", "password") == 900
    # A success keeps the lockout history: the next lockout is still the next level.
    clock.advance(900)
    throttle.record_success("operator")
    assert _fail(throttle, "operator", 5)[-1] == 1800


def test_failures_expire_after_window(throttle, clock, lockouts):
    _fail(throttle, "alice", 4)
    _fail(throttle, "bob", 4)
    clock.advance(899)
    assert throttle.record_failure("alice", "password") == 900  # 899 s old: still counted
    clock.advance(2)  # bob's four are now 901 s old
    assert throttle.record_failure("bob", "password") is None
    assert throttle.check("bob") is None
    assert lockouts == [("user", "alice", 900)]


def test_username_normalized(throttle, lockouts):
    for typed in (" Operator", "OPERATOR ", "\toperator\n", "OpErAtOr", "operator"):
        throttle.record_failure(typed, "password")
    assert lockouts == [("user", "operator", 900)]
    assert throttle.check("  OPERATOR  ") == 900
    assert normalize_username("  Ab C ") == "ab c"
    # Cut to 128 characters after stripping and lower-casing.
    stem = "x" * 128
    assert normalize_username("  " + stem.upper() + "TAIL") == stem
    _fail(throttle, stem + "a", 4)
    assert throttle.record_failure(stem.upper() + "b", "password") == 900
    assert throttle.check(stem) == 900


def test_user_named_global_does_not_lock_everyone(throttle, lockouts):
    assert _fail(throttle, "Global", 5)[-1] == 900
    assert lockouts == [("user", "global", 900)]
    assert throttle.check("global") == 900
    assert throttle.check("anyone") is None


def test_purge_drops_day_old_failures(throttle, db, clock):
    _fail(throttle, "operator", 4)
    clock.advance(DAY - 1)
    throttle.purge()
    assert _rows(db, "login_failures") == 4
    clock.advance(2)
    throttle.purge()
    assert _rows(db, "login_failures") == 0


def test_purge_keeps_lockouts_that_still_matter(db, clock, lockouts):
    throttle = _throttle(db, clock, lockouts)
    _fail(throttle, "operator", 5)
    clock.advance(DAY - 1)
    throttle.purge()  # ended, but still raises the next level
    assert _rows(db, "lockouts") == 1
    assert _fail(throttle, "operator", 5)[-1] == 1800
    clock.advance(DAY + 1)
    throttle.purge()  # ended and forgotten
    assert _rows(db, "lockouts") == 0
    # A lockout longer than a day is kept while it runs, however old it is.
    long_throttle = _throttle(db, clock, lockouts, base_lockout_s=2 * DAY, max_lockout_s=2 * DAY)
    assert _fail(long_throttle, "operator", 5)[-1] == 2 * DAY
    clock.advance(DAY + 1)
    long_throttle.purge()
    assert long_throttle.check("operator") == DAY - 1
