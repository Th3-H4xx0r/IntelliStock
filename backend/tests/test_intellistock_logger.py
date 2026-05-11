import io
import os
import sys

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from intellistock_logger import IntelliStockLogger


def test_logger_replaces_unencodable_console_chars(monkeypatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)

    logger = IntelliStockLogger()
    logger.log("Symbols expanded: 0 \u2192 9", service="TEST")

    stream.flush()
    out = raw.getvalue().decode("cp1252")
    assert "[TEST]" in out
    assert "Symbols expanded: 0 ? 9" in out
