"""Task 13 (spec 5.8) — live rotation buys may spend same-cycle sell proceeds.

Root cause: live sells only free cash when the async trade_updates WS fill
lands (alpaca.py fill handler), but the broker sizes buys later in the SAME
cycle against the adapter's cached cash. A rotation (sell A first, buy B —
sells sort first in `_exec_order`) therefore sized B against pre-sell cash and
starved the paired buy. The backtest emulator credits sell proceeds
synchronously, so this asymmetry is live-only.

Fix: the broker books each submit-SUCCESSFUL live sell's expected proceeds
(qty × fraction × price) and lifts the buy-sizing ceiling by 95% of the booked
total (partial-fill haircut), never exceeding cash + proceeds. Kill-switch:
`live_credit_sell_proceeds_enabled` (default True).

broker.py is NOT import-safe (argparse + sys.exit at module load), so the pure
helper `buy_ceiling` lives in the import-safe `nexus_broker_utils` module and
the wiring is exercised here by a faithful re-play of the broker submit loop
over a fake live adapter. No alert/DB seam is reachable from these pure
helpers, so no cage fixture is required.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus_broker_utils import buy_ceiling  # noqa: E402


# --------------------------------------------------------------------------- #
# Pure-helper unit tests
# --------------------------------------------------------------------------- #

def test_brief_numbers_840_cash_1388_sells():
    # $840 cached cash + $1,388 submitted sells -> $840 + 0.95×$1,388 = $2,158.60
    assert buy_ceiling(840.0, [1388.0]) == 2158.60


def test_disabled_kill_switch_returns_cached_cash():
    assert buy_ceiling(840.0, [1388.0], enabled=False) == 840.0


def test_no_sells_returns_cached_cash():
    assert buy_ceiling(840.0, []) == 840.0
    assert buy_ceiling(840.0, None) == 840.0


def test_multiple_sells_sum_before_haircut():
    # 1000 + 388 = 1388 -> same ceiling as one 1388 sell.
    assert buy_ceiling(840.0, [1000.0, 388.0]) == 2158.60


def test_ceiling_never_exceeds_cash_plus_proceeds():
    # A haircut > 1 is clamped to 1.0 — crediting MORE than the proceeds
    # would let a buy spend money that cannot exist.
    assert buy_ceiling(100.0, [100.0], haircut=1.5) == 200.0
    # And a negative haircut clamps to 0 (never REDUCES the cash).
    assert buy_ceiling(100.0, [100.0], haircut=-0.5) == 100.0


def test_negative_and_zero_proceeds_never_credit():
    assert buy_ceiling(500.0, [-200.0, 0.0]) == 500.0


def test_malformed_entries_ignored_fail_safe():
    assert buy_ceiling(500.0, ["oops", None, 100.0]) == 595.0


def test_malformed_cash_degrades_to_zero():
    assert buy_ceiling("oops", [100.0]) == 95.0
    assert buy_ceiling(None, []) == 0.0


# --------------------------------------------------------------------------- #
# Wiring re-play: fake live adapter through the broker submit-loop semantics
# --------------------------------------------------------------------------- #

class _FakeLiveAdapter:
    """Async-fill live adapter: execute_signal(-1) submits but does NOT move
    cash or positions (the WS fill does that later), mirroring AlpacaAdapter."""

    def __init__(self, cash, positions):
        self._cash = float(cash)
        self._positions = dict(positions)
        self.submitted = []

    def get_cash(self):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def execute_signal(self, ticker, signal, price, sell_fraction=1.0, **_kw):
        self.submitted.append((ticker, signal, price, sell_fraction))
        return True  # submit accepted; fill (cash/position move) is async

    def ws_fill_sell(self, ticker, price):
        """Simulate the async trade_updates fill landing: cash credited,
        position removed — exactly what alpaca.py's fill handler does."""
        qty = self._positions.pop(ticker, 0.0)
        self._cash += qty * price


def _replay_cycle(adapter, order, enabled=True):
    """Faithful re-play of the broker's Task-13 wiring.

    `order` items: (symbol, side, price[, sell_fraction]). Sells run first
    (broker sorts `_sell_first + _buy_rest`). After each submit-successful
    sell, expected proceeds are booked from the CURRENT position mirror (so a
    fill that already landed books zero — no double credit). Returns the
    sizing ceiling each buy saw.
    """
    proceeds = []
    buy_ceilings = {}
    sells = [o for o in order if o[1] == "sell"]
    buys = [o for o in order if o[1] == "buy"]
    for o in sells:
        sym, _side, price = o[0], o[1], o[2]
        frac = o[3] if len(o) > 3 else 1.0
        ok = adapter.execute_signal(sym, -1, price, sell_fraction=frac)
        if ok and enabled:
            qty = float(adapter.get_positions().get(sym, 0.0) or 0.0)
            expected = qty * max(0.0, min(1.0, frac)) * price
            if expected > 0:
                proceeds.append(expected)
    for o in buys:
        sym = o[0]
        cash_now = float(adapter.get_cash() or 0.0)
        ceiling = cash_now
        if proceeds:
            ceiling = buy_ceiling(ceiling, proceeds, enabled=enabled)
        buy_ceilings[sym] = ceiling
    return buy_ceilings


def test_wiring_rotation_buy_sees_credited_ceiling():
    # $840 cash; 40 sh of OLD @ $34.70 = $1,388 expected proceeds.
    adapter = _FakeLiveAdapter(840.0, {"OLD": 40.0})
    order = [("OLD", "sell", 34.70), ("NEW", "buy", 50.0)]
    ceilings = _replay_cycle(adapter, order, enabled=True)
    assert round(ceilings["NEW"], 2) == 2158.60


def test_wiring_kill_switch_disabled_buy_sees_cached_cash_only():
    adapter = _FakeLiveAdapter(840.0, {"OLD": 40.0})
    order = [("OLD", "sell", 34.70), ("NEW", "buy", 50.0)]
    ceilings = _replay_cycle(adapter, order, enabled=False)
    assert ceilings["NEW"] == 840.0


def test_wiring_partial_sell_credits_only_the_sold_fraction():
    # Sell HALF of the position: 20 sh × $34.70 = $694 booked; ×0.95 = $659.30.
    adapter = _FakeLiveAdapter(840.0, {"OLD": 40.0})
    order = [("OLD", "sell", 34.70, 0.5), ("NEW", "buy", 50.0)]
    ceilings = _replay_cycle(adapter, order, enabled=True)
    assert round(ceilings["NEW"], 2) == round(840.0 + 0.95 * 694.0, 2)


def test_wiring_fill_landing_before_booking_never_double_credits():
    # The WS fill lands DURING the submit wait: cash is already credited and
    # the position removed. Booking (which reads positions post-submit) sees
    # qty=0 -> books nothing; the buy sees cash+full proceeds, NOT
    # cash + proceeds + 0.95×proceeds.
    adapter = _FakeLiveAdapter(840.0, {"OLD": 40.0})
    adapter.execute_signal("OLD", -1, 34.70, sell_fraction=1.0)
    adapter.ws_fill_sell("OLD", 34.70)  # fill lands before booking
    proceeds = []
    qty = float(adapter.get_positions().get("OLD", 0.0) or 0.0)
    expected = qty * 1.0 * 34.70
    if expected > 0:
        proceeds.append(expected)
    assert proceeds == []  # nothing booked
    ceiling = buy_ceiling(adapter.get_cash(), proceeds, enabled=True)
    assert round(ceiling, 2) == 2228.0  # 840 + 1388 (real fill), no extra credit


def test_wiring_failed_sell_submit_books_nothing():
    class _FailingAdapter(_FakeLiveAdapter):
        def execute_signal(self, *a, **kw):
            return False

    adapter = _FailingAdapter(840.0, {"OLD": 40.0})
    order = [("OLD", "sell", 34.70), ("NEW", "buy", 50.0)]
    ceilings = _replay_cycle(adapter, order, enabled=True)
    assert ceilings["NEW"] == 840.0


# --------------------------------------------------------------------------- #
# Broker source wiring assertions (broker.py is not import-safe)
# --------------------------------------------------------------------------- #

def _broker_source():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "broker.py")
    with open(path, "r") as fh:
        return fh.read()


def test_broker_imports_and_wires_buy_ceiling():
    src = _broker_source()
    assert "buy_ceiling" in src.split("\n")[0:60][0] or "buy_ceiling" in src  # imported
    # Kill-switch read from strategy config, live-only gates on both seams.
    assert "live_credit_sell_proceeds_enabled" in src
    # 2026-08-08 (bt 559864): both seams now gate on `_scp_credit_on`, which is
    # `(mode == MODE_LIVE) or _scp_bt`. The live behaviour is unchanged; the
    # backtest path is opt-in. See test_backtest_path_is_off_by_default.
    assert src.count("if _scp_credit_on and _scp_sell_proceeds:") == 1  # buy seam
    assert "_scp_credit_on = (mode == MODE_LIVE) or _scp_bt" in src
    assert "decision == -1 and _mpg_submit_ok and _scp_enabled" in src  # sell seam


def test_backtest_path_is_off_by_default():
    """Renamed from test_backtest_path_unchanged (bt 559864).

    The original asserted both seams read `mode == MODE_LIVE` verbatim, on the
    premise that "the backtest emulator's synchronous crediting is untouched".
    That premise was wrong: execution is next-event, so a sell submitted on the
    15:00 bar fills at 16:00 and its proceeds are NOT available to that bar's
    buy. bt 559864 sold $1,845 and funded the paired buy with $125 — a winner
    sized at 12.4% of NAV entered at 2.1%.

    So the seams are now opt-in rather than live-only. What must still hold is
    that an existing backtest is unaffected unless it asks: the flag is read
    with a False default and nothing else can turn it on.
    """
    src = _broker_source()
    assert "backtest_credit_sell_proceeds_enabled" in src
    assert "_scp_bt = False" in src
    assert "_scp_bt = bool(_scp_c.get(\"backtest_credit_sell_proceeds_enabled\"))" in src
    assert "_scp_credit_on = (mode == MODE_LIVE) or _scp_bt" in src
    # the live kill-switch is independent and still guards both seams
    assert "live_credit_sell_proceeds_enabled" in src
    assert "_scp_enabled" in src
