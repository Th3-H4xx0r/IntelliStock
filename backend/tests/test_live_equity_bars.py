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
    other_enabled_run_once_lanes,
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


def test_a_truncated_series_is_served_from_the_longer_last_good():
    """`fetch_alpaca_historical_bars` stitches the window from chunks and
    swallows a failed chunk, so a partial outage returns a SHORT series, not an
    error. A short window under-measures volatility, and under-measured risk
    sizes the 3x core LARGER."""
    long_series = [bar(d, 100.0 + d) for d in range(1, 21)]
    partial = full()
    partial["TQQQ"] = [bar(19, 118.0), bar(20, 119.0)]
    got = build_live_equity_data(lambda s, a, b: partial, UNIVERSE, NOW,
                                 last_good={"TQQQ": long_series})
    assert got["TQQQ"] == long_series
    assert got["TQQQ"] is not long_series


def test_a_series_at_least_as_long_as_the_last_good_is_kept():
    held = [bar(1, 100.0)]
    partial = full()
    partial["TQQQ"] = [bar(1, 100.0), bar(2, 101.0)]
    got = build_live_equity_data(lambda s, a, b: partial, UNIVERSE, NOW,
                                 last_good={"TQQQ": held})
    assert got["TQQQ"] == partial["TQQQ"]


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


# ── spec §8: strategy_eb must be the only enabled run_once lane ──────────────
# graph_nexus_analysis reads `data is not None` as its live/backtest
# discriminator, so bars handed to a shared document flip GNA into backtest
# budget mode on a live tick.


def spec(name, config=None):
    return {"strategy": name, "config": config or {}}


def test_an_eb_only_document_has_no_other_lanes():
    assert other_enabled_run_once_lanes([spec("strategy_eb"),
                                         spec("StrategyEb")]) == []
    assert other_enabled_run_once_lanes([]) == []
    assert other_enabled_run_once_lanes(None) == []


def test_graph_nexus_analysis_alongside_eb_is_reported():
    lanes = other_enabled_run_once_lanes([spec("strategy_eb"),
                                          spec("graph_nexus_analysis")])
    assert lanes == ["graph_nexus_analysis"]


def test_a_lane_disabled_by_its_own_key_does_not_count():
    lanes = other_enabled_run_once_lanes([
        spec("strategy_eb"),
        spec("strategy_x", {"strategy_x_enabled": False}),
        spec("strategy_xs", {"strategy_xs_enabled": "false"}),
        spec("residual_sleeve", {"enabled": 0}),
    ])
    assert lanes == []


def test_a_lane_with_no_enable_key_counts_as_enabled():
    """Fails CLOSED: most run_once strategies — GNA included — have no enable
    key at all, and the failure this guards is silent."""
    assert other_enabled_run_once_lanes([spec("strategy_eb"),
                                         spec("some_new_lane")]) \
        == ["some_new_lane"]
    assert other_enabled_run_once_lanes([
        spec("strategy_x", {"strategy_x_enabled": True})]) == ["strategy_x"]


def test_the_broker_wires_the_carrier_into_the_live_equity_branch():
    """A source assertion: the hook is inline in a 4,000-line function."""
    source = open(os.path.join(_backend, "broker.py")).read()
    assert "build_live_equity_data" in source
    assert "_live_equity_bars_last_good" in source
    assert "other_enabled_run_once_lanes" in source
    assert "[live-equity-bars] strategy_eb must be " in source


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


def test_the_sole_lane_guard_wraps_the_fetch():
    """The §8 guard is not advisory: the bar fetch must be UNREACHABLE when
    another run_once lane is enabled."""
    import ast

    tree = ast.parse(open(os.path.join(_backend, "broker.py")).read())
    crypto = [n for n in ast.walk(tree)
              if isinstance(n, ast.If)
              and "_is_crypto_instance_runtime" in ast.dump(n.test)
              and "_tick_mode" in ast.dump(n.test)]
    assert len(crypto) == 1, "the live crypto bars branch moved"
    equity = crypto[0].orelse[0]
    guards = [n for n in ast.walk(equity)
              if isinstance(n, ast.If) and "_leb_others" in ast.dump(n.test)]
    assert len(guards) == 1, "the sole-lane guard is missing"
    guard = guards[0]

    def dump(nodes):
        return "".join(ast.dump(n) for n in nodes)

    # Other lanes present -> no fetch, and data stays None.
    assert "_leb_build" not in dump(guard.body)
    assert "_leb_fetch" not in dump(guard.body)
    assert "_rr_data" in dump(guard.body)
    # EB alone -> the fetch, and only there.
    assert "_leb_build" in dump(guard.orelse)
    assert "_leb_fetch" in dump(guard.orelse)
    # The lane list comes from the enabled run_once specs, not from a constant.
    assigns = [n for n in ast.walk(equity)
               if isinstance(n, ast.Assign) and "_leb_others" in ast.dump(n)]
    assert assigns and "_run_once_specs" in ast.dump(assigns[0].value)
    # The RED message the operator has to see, reassembled by the parser out of
    # the implicit concatenation the source is line-wrapped into.
    literals = "".join(
        n.value for n in ast.walk(guard.body[-1])
        if isinstance(n, ast.Constant) and isinstance(n.value, str))
    assert ("[live-equity-bars] strategy_eb must be the only enabled run_once "
            "lane on its document; leaving data=None so it refuses to trade."
            ) in literals
    assert "red" in literals
