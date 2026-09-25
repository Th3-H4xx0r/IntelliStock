"""fetch_regime's decision logic (ST tests/test_regime_logic.py, ported to the
pure decision) and the live VIX read with ST's NaN guard."""
import math
import os
import sys
import types

import pandas as pd

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import regime  # noqa: E402

# ST's reference datasets: 299 bars at 400 then 430 -> SMA200 ~= 400.15,
# 430 > 412.15 (allowed); 300 bars at 400 -> 400 > 412 is False (blocked).
ALLOWED = (430.0, (199 * 400.0 + 430.0) / 200)
BLOCKED = (400.0, 400.0)


def test_entries_allowed_when_spy_above_buffer_and_vix_low():
    r = regime.regime_decision(*ALLOWED, 15.0)
    assert r["entries_allowed"] is True and r["blocked_reason"] is None


def test_blocked_when_spy_below_buffer():
    r = regime.regime_decision(*BLOCKED, 15.0)
    assert r["entries_allowed"] is False
    assert r["blocked_reason"] == "SPY below SMA200×1.03"


def test_blocked_when_vix_too_high():
    r = regime.regime_decision(*ALLOWED, 30.0)
    assert r["entries_allowed"] is False and r["blocked_reason"] == "VIX > 25.0"


def test_blocked_when_both_conditions_fail():
    r = regime.regime_decision(*BLOCKED, 30.0)
    assert r["blocked_reason"] == "SPY below SMA200×1.03 and VIX > 25"


def test_vix_exactly_at_threshold_allows_entries():
    assert regime.regime_decision(*ALLOWED, 25.0)["entries_allowed"] is True


def test_missing_or_nan_inputs_block_and_never_propagate_nan():
    for spy, sma, vix in ((None, 400.0, 15.0), (430.0, None, 15.0),
                          (430.0, 400.0, None), (float("nan"), 400.0, 15.0),
                          (430.0, 400.0, float("nan"))):
        r = regime.regime_decision(spy, sma, vix)
        assert r["regime_ok"] is False
        for key in ("spy_close", "spy_sma200", "vix"):
            assert r[key] is None or math.isfinite(r[key])


def test_config_buffer_and_vix_max_are_honoured():
    assert regime.regime_decision(410.0, 400.0, 15.0)["regime_ok"] is False
    assert regime.regime_decision(410.0, 400.0, 15.0, spy_buffer=1.02)["regime_ok"] is True
    assert regime.regime_decision(*ALLOWED, 28.0, vix_max=30.0)["regime_ok"] is True


def _yf(closes=None, error=None):
    class _T:
        def __init__(self, symbol):
            assert symbol == "^VIX"

        def history(self, period, interval):
            if error:
                raise error
            return pd.DataFrame({"Close": closes or []})
    return types.SimpleNamespace(Ticker=_T)


def test_fetch_vix_close_drops_a_trailing_nan(monkeypatch):
    monkeypatch.setattr(regime, "yf", _yf([14.0, 15.5, float("nan")]))
    assert regime.fetch_vix_close() == 15.5


def test_fetch_vix_close_is_none_on_empty_all_nan_or_error(monkeypatch):
    monkeypatch.setattr(regime, "yf", _yf([]))
    assert regime.fetch_vix_close() is None
    monkeypatch.setattr(regime, "yf", _yf([float("nan")]))
    assert regime.fetch_vix_close() is None
    monkeypatch.setattr(regime, "yf", _yf(error=RuntimeError("down")))
    assert regime.fetch_vix_close() is None
