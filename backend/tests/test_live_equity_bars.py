"""The live equity bar carrier: stale bars beat a blind strategy."""
import datetime
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from live_equity_bars import (  # noqa: E402
    LOOKBACK_DAYS_DEFAULT,
    build_live_equity_data,
    lookback_start,
)

NOW = datetime.datetime(2026, 6, 3, 20, 0, tzinfo=datetime.timezone.utc)
UNIVERSE = ["QQQ", "TQQQ", "SPY", "BIL"]


def bar(day, close):
    return {"t": f"2026-06-{day:02d}T05:00:00+00:00", "c": close}


def full(symbols=UNIVERSE):
    return {s: [bar(1, 100.0), bar(2, 101.0)] for s in symbols}


def test_the_window_covers_the_full_lookback():
    start = lookback_start(NOW, 400)
    assert (NOW - start).days == 400
    assert LOOKBACK_DAYS_DEFAULT == 400


def test_a_clean_fetch_is_returned_per_symbol():
    got = build_live_equity_data(lambda s, a, b: full(), UNIVERSE, NOW)
    assert set(got) == set(UNIVERSE)
    assert got["QQQ"][-1]["c"] == 101.0


def test_symbols_are_normalised_deduplicated_and_sorted_for_the_fetch():
    seen = {}

    def fetch(symbols, start, end):
        seen["symbols"] = list(symbols)
        return full()

    build_live_equity_data(fetch, [" spy ", "SPY", "qqq"], NOW)
    assert seen["symbols"] == ["QQQ", "SPY"]


def test_the_fetch_window_ends_now_and_starts_at_the_lookback():
    seen = {}

    def fetch(symbols, start, end):
        seen["start"], seen["end"] = start, end
        return full()

    build_live_equity_data(fetch, UNIVERSE, NOW, lookback_days=30)
    assert seen["end"] == NOW
    assert seen["start"] == NOW - datetime.timedelta(days=30)


def test_an_empty_universe_fetches_nothing():
    def fetch(*_a):
        raise AssertionError("must not fetch")

    assert build_live_equity_data(fetch, [], NOW) == {}


def test_one_empty_symbol_is_backfilled_from_last_good():
    """A transient per-symbol hiccup must not blind the vol transform."""
    partial = full()
    partial["TQQQ"] = []
    got = build_live_equity_data(lambda s, a, b: partial, UNIVERSE, NOW,
                                 last_good={"TQQQ": [bar(1, 55.0)]})
    assert got["TQQQ"][-1]["c"] == 55.0
    assert got["QQQ"][-1]["c"] == 101.0


def test_one_empty_symbol_with_no_last_good_is_simply_empty():
    partial = full()
    partial["BIL"] = []
    got = build_live_equity_data(lambda s, a, b: partial, UNIVERSE, NOW)
    assert got["BIL"] == []


def test_a_total_failure_falls_back_to_the_whole_last_good_snapshot():
    snapshot = full()
    got = build_live_equity_data(lambda s, a, b: None, UNIVERSE, NOW,
                                 last_good=snapshot)
    assert got == snapshot


def test_a_raising_fetch_is_caught_and_falls_back():
    def boom(*_a):
        raise RuntimeError("alpaca 502")

    snapshot = full()
    assert build_live_equity_data(boom, UNIVERSE, NOW,
                                  last_good=snapshot) == snapshot


def test_a_total_failure_with_no_last_good_returns_none():
    """None means: SKIP the tick's strategies. Running them with no data is how
    a held position gets blind-exited."""
    assert build_live_equity_data(lambda s, a, b: None, UNIVERSE, NOW) is None
    assert build_live_equity_data(lambda s, a, b: {}, UNIVERSE, NOW) is None


def test_a_broken_log_callback_never_breaks_the_tick():
    def boom_log(*_a):
        raise RuntimeError("logger is down")

    snapshot = full()
    assert build_live_equity_data(lambda s, a, b: None, UNIVERSE, NOW,
                                  last_good=snapshot,
                                  log=boom_log) == snapshot


def test_backfilled_bar_lists_are_copies_not_aliases():
    """The caller stores the result AS the next last-good, so a backfilled list
    must not alias the snapshot it came from."""
    snapshot = {"TQQQ": [bar(1, 55.0)]}
    partial = full()
    partial["TQQQ"] = []
    got = build_live_equity_data(lambda s, a, b: partial, UNIVERSE, NOW,
                                 last_good=snapshot)
    assert got["TQQQ"] == snapshot["TQQQ"]
    assert got["TQQQ"] is not snapshot["TQQQ"]


def test_the_broker_wires_the_carrier_into_the_live_equity_branch():
    """A source assertion: the hook is inline in a 4,000-line function."""
    source = open(os.path.join(_backend, "broker.py")).read()
    assert "build_live_equity_data" in source
    assert "_live_equity_bars_last_good" in source


def test_the_hook_is_a_live_sibling_of_the_crypto_branch():
    """Not just present in the file: an `elif` on the crypto branch, inside the
    live-only block, guarded by the strategy_eb universe. A string match would
    pass on dead code."""
    import ast

    tree = ast.parse(open(os.path.join(_backend, "broker.py")).read())
    crypto_ifs = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.If)
        and "_is_crypto_instance_runtime" in ast.dump(n.test)
        and "_tick_mode" in ast.dump(n.test)
    ]
    assert len(crypto_ifs) == 1, "the live crypto bars branch moved"
    orelse = crypto_ifs[0].orelse
    assert len(orelse) == 1 and isinstance(orelse[0], ast.If), \
        "the equity carrier must be an elif on the crypto branch"
    equity = ast.dump(orelse[0])
    assert "'IDLE'" in ast.dump(orelse[0].test)
    assert "_strategy_eb_universe_symbols" in equity
    assert "build_live_equity_data" in equity
    assert "_live_equity_bars_last_good" in equity
    # A failed fetch must empty the specs, not run the strategies blind.
    assert "_rr_specs_eff" in equity


def test_the_hook_lives_in_the_live_only_block():
    import ast

    tree = ast.parse(open(os.path.join(_backend, "broker.py")).read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if "MODE_LIVE" not in ast.dump(node.test):
            continue
        if "build_live_equity_data" in ast.dump(node):
            return
    raise AssertionError("the carrier is not inside an `if mode == MODE_LIVE` "
                         "block — a backtest must not take this path")
