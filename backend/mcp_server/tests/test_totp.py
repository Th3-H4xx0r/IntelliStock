"""TOTP: six digits, 30 s steps, +-1 step, a consumed step never accepted twice, secrets sealed per user."""
import threading
from urllib.parse import parse_qs, unquote, urlsplit

import pyotp
import pytest

from conftest import FakeClock
from mcp_server.authdb import AuthDB
from mcp_server.crypto import CryptoError, derive_key, unseal
from mcp_server.totp import TotpService

T = 60_000_000  # the step under test: 1_800_000_000 to 1_800_000_030
START = T * 30 + 15.0  # mid-step
USER = "u-123"
SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"  # fixed, so every code below is deterministic
BASE32 = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


@pytest.fixture
def clock():
    return FakeClock(START)


@pytest.fixture
def db(settings):
    store = AuthDB(settings.db_path)
    yield store
    store.close()


@pytest.fixture
def totp(db, settings, clock):
    return TotpService(db, settings.master_key, clock=clock)


def _enroll(db, totp, user_id=USER, username="operator", secret=SECRET, aad=None):
    """A users row as enrollment writes it: the secret sealed with aad = user_id."""
    db.query(
        "INSERT INTO users (user_id, username, enrolled_at, totp_secret_enc, totp_last_step) VALUES (?, ?, ?, ?, 0)",
        (user_id, username, START, totp.seal(secret, aad or user_id)),
    )


@pytest.fixture
def enrolled(db, totp):
    _enroll(db, totp)


def _code(step, secret=SECRET):
    return pyotp.TOTP(secret).generate_otp(step)


def _last_step(db, user_id=USER):
    return db.query_one("SELECT totp_last_step FROM users WHERE user_id = ?", (user_id,))["totp_last_step"]


def test_fixed_secret_codes_are_distinct():
    # Precondition for the window tests: no two of these steps share a code.
    assert len({_code(step) for step in range(T - 2, T + 3)}) == 5


def test_new_secret_is_32_base32_characters(totp):
    first, second = totp.new_secret(), totp.new_secret()
    assert len(first) == 32 and set(first) <= BASE32
    assert first != second


def test_accepts_current_and_adjacent_steps(totp, db, enrolled, clock):
    for step in (T - 1, T, T + 1):
        assert totp.match_step(SECRET, _code(step)) == step
    # What an authenticator app shows 30 s either side of the server's clock.
    for offset in (-30, 0, 30):
        assert totp.match_step(SECRET, pyotp.TOTP(SECRET).at(START + offset)) == T + offset // 30
    for step in (T - 1, T, T + 1):  # oldest first, so each is newer than the last one spent
        assert totp.verify_and_consume(USER, _code(step)) is True
        assert _last_step(db) == step


def test_rejects_two_steps_away(totp, db, enrolled, clock):
    for step in (T - 2, T + 2, T - 40, T + 40):
        assert totp.match_step(SECRET, _code(step)) is None
        assert totp.verify_and_consume(USER, _code(step)) is False
    assert _last_step(db) == 0
    clock.advance(30)  # one step later, T + 2 is adjacent
    assert totp.verify_and_consume(USER, _code(T + 2)) is True


def test_step_never_accepted_twice(totp, db, enrolled, clock):
    code = _code(T)
    assert totp.verify_and_consume(USER, code) is True
    assert totp.verify_and_consume(USER, code) is False  # replayed inside its own step
    clock.advance(30)  # T is now the previous step: still inside the window, still spent
    assert totp.match_step(SECRET, code) == T
    assert totp.verify_and_consume(USER, code) is False
    # A later step spent first shuts out every earlier one.
    assert totp.verify_and_consume(USER, _code(T + 2)) is True
    assert totp.verify_and_consume(USER, _code(T + 1)) is False
    assert _last_step(db) == T + 2


def test_consume_is_a_compare_and_swap(totp, db, enrolled, monkeypatch):
    # Another worker spends the step between this call's read of the row and its write.
    real_match = totp.match_step

    def racing_match(secret, code):
        step = real_match(secret, code)
        db.query("UPDATE users SET totp_last_step = ? WHERE user_id = ?", (step, USER))
        return step

    monkeypatch.setattr(totp, "match_step", racing_match)
    assert totp.verify_and_consume(USER, _code(T)) is False
    assert _last_step(db) == T


def test_two_connections_spend_one_code_once(totp, db, enrolled, settings, clock):
    # The server and the admin CLI each open the file; one code still signs in once.
    other_db = AuthDB(settings.db_path)
    try:
        services = [totp, TotpService(other_db, settings.master_key, clock=clock)]
        code = _code(T)
        barrier = threading.Barrier(2)
        results = []

        def attempt(service):
            barrier.wait()
            results.append(service.verify_and_consume(USER, code))

        threads = [threading.Thread(target=attempt, args=(service,)) for service in services]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(results) == [False, True]
    finally:
        other_db.close()


def test_rejects_non_six_digit_codes(totp, db, enrolled):
    good = _code(T)
    # The same code in other scripts' digits: str \d matches them, hmac.compare_digest raises on them.
    arabic_indic = good.translate({ord("0") + n: 0x0660 + n for n in range(10)})
    fullwidth = good.translate({ord("0") + n: 0xFF10 + n for n in range(10)})
    bad = ["", good[:5], good + "0", "0" + good, " " + good, good + " ", good + "\n", good[:3] + " " + good[3:],
           good[:5] + "a", "-" + good[1:], arabic_indic, fullwidth, None, int(good), good.encode("ascii")]
    for code in bad:
        assert totp.match_step(SECRET, code) is None, repr(code)
        assert totp.verify_and_consume(USER, code) is False, repr(code)
    assert _last_step(db) == 0
    assert totp.verify_and_consume(USER, good) is True  # the same code, well formed


def test_unknown_user_is_refused(totp, enrolled):
    assert totp.verify_and_consume("u-nobody", _code(T)) is False


def test_secret_sealed_with_user_aad(totp, db, settings):
    secret = totp.new_secret()
    blob = totp.seal(secret, USER)
    assert secret.encode("ascii") not in blob
    assert totp.unseal(blob, USER) == secret
    with pytest.raises(CryptoError):
        totp.unseal(blob, "u-other")
    # Under the "totp" subkey, and no other.
    assert unseal(derive_key(settings.master_key, "totp"), blob, USER) == secret.encode("ascii")
    with pytest.raises(CryptoError):
        unseal(derive_key(settings.master_key, "refresh-grace"), blob, USER)
    # A blob sealed for one user and copied onto another user's row does not open: an error, not a wrong code.
    _enroll(db, totp, user_id="u-other", username="other", secret=secret, aad=USER)
    with pytest.raises(CryptoError):
        totp.verify_and_consume("u-other", _code(T, secret))
    assert _last_step(db, "u-other") == 0


def test_provisioning_uri_has_issuer_and_username(totp, db, settings, clock):
    uri = totp.provisioning_uri(SECRET, "operator")
    parts = urlsplit(uri)
    assert (parts.scheme, parts.netloc) == ("otpauth", "totp")
    assert unquote(parts.path) == "/IntelliStock MCP:operator"
    query = parse_qs(parts.query)
    assert query["secret"] == [SECRET]
    assert query["issuer"] == ["IntelliStock MCP"]
    # An authenticator app reading it gets 6 digits every 30 s, the parameters the server checks.
    parsed = pyotp.parse_uri(uri)
    assert (parsed.secret, parsed.name, parsed.issuer) == (SECRET, "operator", "IntelliStock MCP")
    assert (parsed.digits, parsed.interval) == (6, 30)
    assert totp.match_step(SECRET, parsed.at(START)) == T
    named = TotpService(db, settings.master_key, clock=clock, issuer_name="IntelliStock Lab")
    assert pyotp.parse_uri(named.provisioning_uri(SECRET, "operator")).issuer == "IntelliStock Lab"
