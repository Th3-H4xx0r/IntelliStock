"""Logger secret-redaction."""
import os
import sys

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from intellistock_logger import IntelliStockLogger, _redact


def test_redact_apca_key_id():
    out = _redact("APCA-API-KEY-ID=PKTEST1234567890ABCDE rest")
    assert "PKTEST1234567890ABCDE" not in out
    assert "REDACTED" in out


def test_redact_apca_secret():
    out = _redact("APCA-API-SECRET-KEY: abc123def456ghi789jkl012mno345pqr678stu9")
    assert "abc123def456" not in out
    assert "REDACTED" in out


def test_redact_bearer():
    out = _redact("Authorization: Bearer abcdefghij1234567890.refresh_token_section")
    assert "abcdefghij1234567890.refresh_token_section" not in out
    assert "REDACTED" in out


def test_redact_standalone_alpaca_key():
    out = _redact("using key PKTEST1234567890ABCDE for request")
    assert "PKTEST1234567890ABCDE" not in out


def test_redact_refresh_token_kv():
    out = _redact("refresh_token=abcde12345fghij67890")
    assert "abcde12345fghij67890" not in out


def test_redact_preserves_non_secrets():
    out = _redact("Normal log line AAPL $150.25")
    assert out == "Normal log line AAPL $150.25"


def test_logger_redacts_through_log_method(capsys):
    log = IntelliStockLogger()
    log.log("APCA-API-KEY-ID=PKTEST1234567890ABCDE leak")
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "PKTEST1234567890ABCDE" not in combined
    assert "REDACTED" in combined
