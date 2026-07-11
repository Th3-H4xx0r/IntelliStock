"""Wiring regression tests for crypto instances (same-codebase, kind='crypto').

These lock in the SHARED-broker integration seams (loader, strategy listing,
symbol validation, 24/7 scheduler acceptance) that live outside the pure
strategy module, so a refactor can't silently break crypto instance loading.
Pure/dependency-light — no network, no alpaca, no DB.
"""
from __future__ import annotations

import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scheduler  # noqa: E402
from strategies.crypto import core  # noqa: E402
from strategies_meta import get_available_strategies  # noqa: E402
from ticker_universe import is_valid_crypto_pair, is_valid_us_ticker  # noqa: E402

CRYPTO_STRATS = {"reference": "Reference", "fast": "Fast", "momentum": "Momentum", "allocator": "Allocator"}


def test_crypto_pair_validator_accepts_pairs_rejects_equities():
    assert is_valid_crypto_pair("BTC/USD") is True
    assert is_valid_crypto_pair("ETH/USDT") is True
    assert is_valid_crypto_pair("SHIB/USD") is True
    assert is_valid_crypto_pair("AAPL") is False
    assert is_valid_crypto_pair("BTC-USD") is False
    assert is_valid_crypto_pair("") is False
    # The equity validator must still reject the slash (unchanged behavior).
    assert is_valid_us_ticker("BTC/USD", strict=False) is False
    assert is_valid_us_ticker("AAPL", strict=False) is True


def test_available_strategies_lists_crypto_as_run_once_and_hides_helpers():
    by_id = {s["id"]: s for s in get_available_strategies()}
    for mod in CRYPTO_STRATS:
        assert mod in by_id, f"crypto strategy {mod} not listed"
        assert by_id[mod]["schema"].get("execution_scope") == "run_once"
    # Helper modules must NOT be surfaced as strategies.
    assert "core" not in by_id
    assert "discovery" not in by_id


def _resolve_like_broker(module_name):
    """Mimic broker._load_strategy_class: flat path first, then crypto subpackage."""
    for mod_path in ("strategies." + module_name, "strategies.crypto." + module_name):
        try:
            spec = importlib.util.find_spec(mod_path)
        except (ImportError, AttributeError, ValueError):
            spec = None
        if spec is not None and spec.origin is not None:
            return spec
    return None


def test_loader_resolves_crypto_strategies_with_run_once():
    for mod, cls in CRYPTO_STRATS.items():
        spec = _resolve_like_broker(mod)
        assert spec is not None, f"{mod} not resolvable"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        klass = getattr(module, cls, None)
        assert klass is not None, f"class {cls} missing in {mod}"
        assert hasattr(klass, "run_once"), f"{cls} is not a run_once strategy"


def test_crypto_scheduler_config_is_accepted_24_7_by_real_scheduler():
    import datetime as dt
    cfg = core.crypto_scheduler_config("medium")
    # A Saturday (weekend) — equity config would sleep to Monday; crypto must
    # schedule a next wake, proving weekends_only=False + 0..1440 window validate.
    sat = dt.datetime(2026, 7, 11, 20, 0, tzinfo=dt.timezone.utc)
    nxt, mode = scheduler.get_next_wake(sat, marker=None, config=cfg)
    assert nxt > sat
    assert mode in ("FULL", "MONITOR", "IDLE")


def test_build_crypto_order_obeys_alpaca_crypto_rules():
    o = core.build_crypto_order("BTC/USD", "buy", 0.01, prefer_maker=True, last_price=50000.0)
    assert o["tif"] == "gtc"
    assert o["extended_hours"] is False
    assert o["order_type"] == "limit"
    assert o["limit_price"] < 50000.0  # maker buy rests below the touch
    t = core.build_crypto_order("BTC/USD", "sell", 0.01, prefer_maker=False)
    assert t["tif"] == "gtc" and t["extended_hours"] is False and t["order_type"] == "market"
