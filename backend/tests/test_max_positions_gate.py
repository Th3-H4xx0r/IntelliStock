"""Task 7 — hard max_positions gate at order-emission time.

Root cause (bt 586767 held 13 vs cap 10): GNA sizes NEW-name buys across several
independent paths (main allocation, BFQ direct-reserved, BFQ dequeue,
momentum-window adds) each against its OWN local headroom snapshot, and rotation
buys are cap-EXEMPT on the assumption their paired full-exit sell nets the count
to zero. Nothing recounts the TOTAL at emission, so the paths' sums — plus any
rotation whose sell didn't fully net — overshot the cap. The fix is a single
authoritative emission-time recount: `max_positions_gate`.

broker.py is NOT import-safe (argparse + sys.exit at module load — see
test_backtest_credit_pause.py / Task 3), so the pure helper lives in the already
import-safe `nexus_broker_utils` module and the "wiring" is exercised here by a
faithful re-play of the broker emission loop over a fake emulator + order list.
No alert/DB seam is reachable from these pure helpers, so no cage fixture is
required.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus_broker_utils import (  # noqa: E402
    max_positions_arm_warning,
    max_positions_gate,
    max_positions_projected_count,
    resolve_max_positions_cap,
)
from tests.nexus_real_config import real_config  # noqa: E402


# --------------------------------------------------------------------------- #
# Pure-helper unit tests
# --------------------------------------------------------------------------- #

def test_under_cap_new_name_allowed():
    held = {f"H{i}" for i in range(5)}
    assert max_positions_gate(held, 10, set(), set(), "NEW") is True


def test_at_cap_new_name_blocked():
    held = {f"H{i}" for i in range(10)}
    assert max_positions_gate(held, 10, set(), set(), "NEW") is False


def test_eleventh_new_name_blocked_with_cap_ten():
    # 10 held, no exits, no new emitted yet -> projected 10 >= cap 10 -> block.
    held = {f"H{i}" for i in range(10)}
    assert max_positions_gate(held, 10, set(), set(), "NUMBER11") is False
    assert max_positions_projected_count(held, set(), set()) == 10


def test_add_to_existing_name_exempt_even_at_cap():
    # An add / winner-add / amplifier-add to a name already held never grows the
    # count and must always be allowed, even when the portfolio is at the cap.
    held = {f"H{i}" for i in range(12)}  # already OVER cap
    assert max_positions_gate(held, 10, set(), set(), "H3") is True


def test_rotation_pair_nets_to_zero():
    # At the cap: full-exit sell of A frees a slot, so a NEW name B may enter.
    held = {f"H{i}" for i in range(10)}
    full_exits = {"H0"}  # A fully exited this cycle
    assert max_positions_gate(held, 10, full_exits, set(), "B_NEW") is True


def test_partial_sell_does_not_free_a_slot():
    # A sell that is NOT a full exit must not create headroom for a new name.
    held = {f"H{i}" for i in range(10)}
    partial_only = set()  # partial sell -> broker never adds it to full_exits
    assert max_positions_gate(held, 10, partial_only, set(), "B_NEW") is False


def test_two_full_exits_admit_two_new_names_but_not_a_third():
    held = {f"H{i}" for i in range(10)}
    full_exits = {"H0", "H1"}
    # first new name allowed
    assert max_positions_gate(held, 10, full_exits, set(), "N1") is True
    # after N1 emitted, second new name allowed
    assert max_positions_gate(held, 10, full_exits, {"N1"}, "N2") is True
    # third new name blocked (projected 8 held + 2 emitted = 10 >= 10)
    assert max_positions_gate(held, 10, full_exits, {"N1", "N2"}, "N3") is False


def test_already_emitted_candidate_is_idempotently_allowed():
    held = {f"H{i}" for i in range(10)}
    # NEW was already emitted this cycle; re-evaluating must not double-block it.
    assert max_positions_gate(held, 10, set(), {"NEW"}, "NEW") is True


def test_case_and_whitespace_insensitive():
    held = {"AAPL"}
    assert max_positions_gate(held, 10, set(), set(), " aapl ") is True  # add-exempt
    held10 = {f"H{i}" for i in range(9)} | {"MSFT"}
    assert max_positions_gate(held10, 10, {" msft "}, set(), "NEW") is True  # exit nets


def test_cap_zero_blocks_all_new_names_but_allows_adds():
    held = {"AAA", "BBB"}
    assert max_positions_gate(held, 0, set(), set(), "NEW") is False
    assert max_positions_gate(held, 0, set(), set(), "AAA") is True  # add still ok


def test_fail_open_on_malformed_cap():
    # A non-numeric cap must not crash the cycle — fail open (allow).
    assert max_positions_gate({"A"}, "not-a-number", set(), set(), "NEW") is True


def test_fail_open_on_non_iterable_held():
    assert max_positions_gate(object(), 10, set(), set(), "NEW") is True


def test_none_cap_allows():
    # No configured cap -> gate inert (broker skips the gate anyway).
    assert max_positions_gate({"A"}, None, set(), set(), "NEW") is True


def test_projected_count_fail_open_returns_negative_one():
    assert max_positions_projected_count(object(), set(), set()) == -1


# --------------------------------------------------------------------------- #
# Wiring test: faithful re-play of the broker emission loop
# --------------------------------------------------------------------------- #

class _FakeEmulator:
    """Minimal stand-in for PortfolioEmulator's held-symbol surface."""

    def __init__(self, positions):
        self._positions = dict(positions)

    def get_positions(self):
        return dict(self._positions)


def _held_snapshot(emu):
    """Mirror broker's cycle-start held snapshot (qty > 0)."""
    return {s for s, q in emu.get_positions().items() if float(q or 0.0) > 0.0}


def _replay_emission(emu, cap, order):
    """Re-play broker's `for symbol in _exec_order` gate.

    `order` items: (symbol, side, full_exit[, submit_ok]) with side in
    {"buy","sell"}; ``submit_ok`` (default True) models execute_signal/submit
    success. Sells are processed first (broker sorts `_sell_first + _buy_rest`)
    and — per review IMPORTANT 2 — a full-exit sell credits its slot ONLY when
    the submit succeeded. Returns (emitted_buys, blocked_buys, log_lines).
    """
    held0 = _held_snapshot(emu)
    full_exits = set()
    emitted_new = set()
    emitted_buys = []
    blocked_buys = []
    log_lines = []

    sells = [o for o in order if o[1] == "sell"]
    buys = [o for o in order if o[1] == "buy"]
    for o in sells:
        sym, _side, full_exit = o[0], o[1], o[2]
        submit_ok = o[3] if len(o) > 3 else True
        if full_exit and submit_ok:
            full_exits.add(sym)
    for o in buys:
        sym = o[0]
        if not max_positions_gate(held0, cap, full_exits, emitted_new, sym):
            proj = max_positions_projected_count(held0, full_exits, emitted_new)
            log_lines.append(f"MAX_POSITIONS_GATE: blocked {sym} (held={proj}, cap={cap})")
            blocked_buys.append(sym)
            continue
        emitted_buys.append(sym)
        if sym.upper() not in {h.upper() for h in held0}:
            emitted_new.add(sym)
    return emitted_buys, blocked_buys, log_lines


def test_wiring_eleventh_new_name_blocked_and_logged():
    cap = int(real_config()["max_positions"])  # real doc-179 tune -> 10
    assert cap == 10
    emu = _FakeEmulator({})  # empty book
    order = [(f"NEW{i}", "buy", False) for i in range(13)]
    emitted, blocked, logs = _replay_emission(emu, cap, order)
    assert len(emitted) == 10
    assert blocked == ["NEW10", "NEW11", "NEW12"]
    assert logs[0] == "MAX_POSITIONS_GATE: blocked NEW10 (held=10, cap=10)"


def test_wiring_adds_to_existing_positions_never_blocked():
    cap = int(real_config()["max_positions"])
    held = {f"H{i}": 10.0 for i in range(10)}  # already at cap
    emu = _FakeEmulator(held)
    # 3 adds to existing names + 1 new name. Adds pass; new name blocked.
    order = [("H0", "buy", False), ("H5", "buy", False), ("H9", "buy", False),
             ("BRANDNEW", "buy", False)]
    emitted, blocked, logs = _replay_emission(emu, cap, order)
    assert set(emitted) == {"H0", "H5", "H9"}
    assert blocked == ["BRANDNEW"]


def test_wiring_rotation_pair_at_cap_admits_the_paired_buy():
    cap = int(real_config()["max_positions"])
    held = {f"H{i}": 10.0 for i in range(10)}  # at cap
    emu = _FakeEmulator(held)
    # Rotation: full-exit sell of H0 funds a NEW name; nets to zero -> allowed.
    order = [("H0", "sell", True), ("ROTIN", "buy", False)]
    emitted, blocked, logs = _replay_emission(emu, cap, order)
    assert emitted == ["ROTIN"]
    assert blocked == []


def test_wiring_rotation_with_partial_sell_blocks_the_buy():
    cap = int(real_config()["max_positions"])
    held = {f"H{i}": 10.0 for i in range(10)}
    emu = _FakeEmulator(held)
    # Partial trim (full_exit=False) does NOT free a slot -> new name blocked.
    order = [("H0", "sell", False), ("ROTIN", "buy", False)]
    emitted, blocked, logs = _replay_emission(emu, cap, order)
    assert emitted == []
    assert blocked == ["ROTIN"]
    assert logs == ["MAX_POSITIONS_GATE: blocked ROTIN (held=10, cap=10)"]


def test_wiring_live_failed_funding_sell_blocks_the_paired_buy():
    # Review IMPORTANT 2: a live rotation whose funding sell FAILS to submit
    # must NOT credit the freed slot — otherwise the paired buy lands cap+1.
    cap = int(real_config()["max_positions"])
    held = {f"H{i}": 10.0 for i in range(10)}  # at cap
    emu = _FakeEmulator(held)
    order = [("H0", "sell", True, False),  # planned full exit, submit FAILED
             ("ROTIN", "buy", False)]
    emitted, blocked, logs = _replay_emission(emu, cap, order)
    assert emitted == []
    assert blocked == ["ROTIN"]
    assert logs == ["MAX_POSITIONS_GATE: blocked ROTIN (held=10, cap=10)"]


def test_wiring_successful_funding_sell_still_admits_the_paired_buy():
    # Counterpart of the failure case: submit_ok=True keeps rotation working.
    cap = int(real_config()["max_positions"])
    held = {f"H{i}": 10.0 for i in range(10)}
    emu = _FakeEmulator(held)
    order = [("H0", "sell", True, True), ("ROTIN", "buy", False)]
    emitted, blocked, _logs = _replay_emission(emu, cap, order)
    assert emitted == ["ROTIN"]
    assert blocked == []


# --------------------------------------------------------------------------- #
# Cap resolution + arm warning (review IMPORTANT 1 seam + MINOR)
# --------------------------------------------------------------------------- #

def _nexus_spec(config):
    return {"strategy": "graph_nexus_analysis", "config": config}


def test_resolve_cap_from_real_config():
    cap, reason = resolve_max_positions_cap([_nexus_spec(real_config())])
    assert (cap, reason) == (10, "armed")


@pytest.mark.parametrize("disabled_weight", [0.0, -1.0])
def test_resolve_cap_ignores_explicitly_disabled_nexus_specs(disabled_weight):
    disabled = _nexus_spec({"max_positions": 6})
    disabled["weight"] = disabled_weight
    assert resolve_max_positions_cap([disabled]) == (None, "no_nexus_spec")

    enabled = _nexus_spec({"max_positions": 11})
    enabled["weight"] = 1.0
    assert resolve_max_positions_cap([disabled, enabled]) == (11, "armed")


def test_resolve_cap_missing_key_and_warning_line():
    cfg = real_config()
    cfg.pop("max_positions", None)
    cap, reason = resolve_max_positions_cap([_nexus_spec(cfg)])
    assert cap is None
    assert reason == "no_max_positions_key"
    # MINOR review item: the exact operator-facing warning line.
    assert max_positions_arm_warning(reason) == (
        "MAX_POSITIONS_GATE not armed: no max_positions in config"
    )


def test_resolve_cap_no_nexus_spec_is_silent():
    cap, reason = resolve_max_positions_cap([{"strategy": "other", "config": {}}])
    assert cap is None
    assert reason == "no_nexus_spec"
    assert max_positions_arm_warning(reason) is None
    # Empty / None spec lists too.
    assert resolve_max_positions_cap([]) == (None, "no_nexus_spec")
    assert resolve_max_positions_cap(None) == (None, "no_nexus_spec")


def test_resolve_cap_invalid_value_warns():
    cap, reason = resolve_max_positions_cap([_nexus_spec({"max_positions": "ten"})])
    assert cap is None
    assert reason == "invalid"
    assert "not armed" in (max_positions_arm_warning(reason) or "")


def test_arm_warning_silent_when_armed():
    assert max_positions_arm_warning("armed") is None


# --------------------------------------------------------------------------- #
# Pending-trade path re-play (review IMPORTANT 1)
# --------------------------------------------------------------------------- #

def _replay_pending_block(emu, cached_strategies, pending):
    """Re-play the broker pending-trade block gate (start-of-bar / same-bar).

    ``pending`` items: (symbol, signal, sell_frac, submit_ok). Sells sort first
    (broker sorts pending by signal). Mirrors broker.py: cap via
    resolve_max_positions_cap, held snapshot at block entry, NEW-name buys
    gated, full-exit sells credited only on execute success.
    """
    cap, reason = resolve_max_positions_cap(cached_strategies)
    held = _held_snapshot(emu) if cap is not None else set()
    full_exits = set()
    new_emitted = set()
    executed = []
    logs = []
    warn = max_positions_arm_warning(reason)
    if warn:
        logs.append(warn)
    pending = sorted(pending, key=lambda t: (0 if t[1] == -1 else 1))
    for sym, sig, sell_frac, submit_ok in pending:
        if sig == 1:
            if cap is not None and not max_positions_gate(held, cap, full_exits, new_emitted, sym):
                proj = max_positions_projected_count(held, full_exits, new_emitted)
                logs.append("MAX_POSITIONS_GATE: blocked %s (held=%d, cap=%d)" % (sym, proj, cap))
                continue
            executed.append(("buy", sym))
            if cap is not None and sym not in held:
                new_emitted.add(sym)
        else:
            executed.append(("sell", sym))
            if cap is not None and submit_ok and sell_frac >= 0.999 and sym in held:
                full_exits.add(sym)
    return executed, logs


def test_pending_path_buy_blocked_at_cap():
    # Review IMPORTANT 1: pending-trade (queue drain) buys run BEFORE the main
    # emission loop and must hit the same gate. At cap, a new-name pending buy
    # is blocked and logged; an add to a held name still executes.
    specs = [_nexus_spec(real_config())]  # cap = 10
    held = {f"H{i}": 10.0 for i in range(10)}
    emu = _FakeEmulator(held)
    pending = [("EARNNEW", 1, 1.0, True),  # new name -> blocked
               ("H4", 1, 1.0, True)]       # add to held -> executes
    executed, logs = _replay_pending_block(emu, specs, pending)
    assert ("buy", "H4") in executed
    assert ("buy", "EARNNEW") not in executed
    assert "MAX_POSITIONS_GATE: blocked EARNNEW (held=10, cap=10)" in logs


def test_pending_path_full_exit_sell_frees_slot_for_pending_buy():
    specs = [_nexus_spec(real_config())]
    held = {f"H{i}": 10.0 for i in range(10)}
    emu = _FakeEmulator(held)
    # Sell sorts first (signal -1), fully exits H0 successfully -> buy admitted.
    pending = [("EARNNEW", 1, 1.0, True), ("H0", -1, 1.0, True)]
    executed, logs = _replay_pending_block(emu, specs, pending)
    assert ("buy", "EARNNEW") in executed
    assert logs == []


def test_pending_path_failed_sell_does_not_free_slot():
    specs = [_nexus_spec(real_config())]
    held = {f"H{i}": 10.0 for i in range(10)}
    emu = _FakeEmulator(held)
    pending = [("EARNNEW", 1, 1.0, True), ("H0", -1, 1.0, False)]  # sell fails
    executed, logs = _replay_pending_block(emu, specs, pending)
    assert ("buy", "EARNNEW") not in executed
    assert "MAX_POSITIONS_GATE: blocked EARNNEW (held=10, cap=10)" in logs


def test_pending_path_missing_cap_warns_and_does_not_block():
    cfg = real_config()
    cfg.pop("max_positions", None)
    specs = [_nexus_spec(cfg)]
    emu = _FakeEmulator({f"H{i}": 10.0 for i in range(10)})
    pending = [("EARNNEW", 1, 1.0, True)]
    executed, logs = _replay_pending_block(emu, specs, pending)
    assert ("buy", "EARNNEW") in executed  # gate inert (fail-open)
    assert logs[0] == "MAX_POSITIONS_GATE not armed: no max_positions in config"
