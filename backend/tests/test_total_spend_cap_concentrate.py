"""Concentrate the satellite budget instead of shaving every candidate.

bt 496659 opened with the satellite's $2,280 (its 38% design share) spread
across 5 sized names: every one scaled by 0.812 to ~$456 = 7.6% of NAV. The
objective's own measurement of the book is mean 6.75% / median 4.73%, against a
stated target of 10-15% -- "a +100% name at a 2% position is noise; at 10-15%
it is the year".

Uniform scaling means the MORE good ideas the book has, the LESS each one can
pay. These tests pin the replacement: rank by conviction, fund from the top at a
real weight until the budget is spent, drop the rest to the queue.
"""
import pytest


MIN_POS = 100.0


def _allocate(sizes, cap, nav, target_pct=0.0, concentrate=True, held=frozenset()):
    """Mirror of the V31.2 total-spend cap, both branches."""
    cands = []
    for sym, hint in sizes.items():
        if sym.upper() in held:
            continue
        cash = float(hint.get("buy_cash", 0.0) or 0.0)
        if cash > 0:
            cands.append((float(hint.get("raw_net_score", 0.0) or 0.0), cash, sym))
    total = sum(c[1] for c in cands)
    out = dict(sizes)
    if total <= cap:
        return out

    if not concentrate:
        scale = cap / total
        for _s, cash, sym in cands:
            scaled = cash * scale
            if scaled < MIN_POS:
                out.pop(sym, None)
            else:
                out[sym] = {**out[sym], "buy_cash": round(scaled, 2)}
        return out

    target = nav * target_pct if target_pct > 0 else 0.0
    floor = target if target > 0 else MIN_POS
    cands.sort(key=lambda t: (-t[0], -t[1], t[2]))
    left = cap
    for _s, cash, sym in cands:
        want = max(cash, target) if target > 0 else cash
        take = min(want, left)
        if take < floor or take < MIN_POS:
            out.pop(sym, None)
            continue
        out[sym] = {**out[sym], "buy_cash": round(take, 2)}
        left -= take
    return out


def _bt496659():
    """The real opening bar: 5 names, $561 requested each, $2,280 cap."""
    names = ["NXT", "WDC", "CPER", "VOYA", "RGEN"]
    return {n: {"buy_cash": 561.4, "raw_net_score": s}
            for n, s in zip(names, [1.8, 1.7, 1.2, 0.9, 0.6])}


def test_the_bug_uniform_scaling_puts_nobody_at_size():
    out = _allocate(_bt496659(), 2280.0, 6000.0, concentrate=False)
    weights = [v["buy_cash"] / 6000.0 for v in out.values()]
    assert len(out) == 5
    assert all(w < 0.08 for w in weights)
    assert max(weights) == pytest.approx(0.076, abs=0.002)


def test_concentrate_at_12pct_puts_three_names_at_target():
    out = _allocate(_bt496659(), 2280.0, 6000.0, target_pct=0.12)
    weights = {k: v["buy_cash"] / 6000.0 for k, v in out.items()}
    assert set(out) == {"NXT", "WDC", "CPER"}
    assert all(w == pytest.approx(0.12) for w in weights.values())
    # $120 of the $2,280 is deliberately left as cash rather than spent on a
    # 2%-of-NAV runt — the objective calls that noise.
    assert sum(v["buy_cash"] for v in out.values()) == pytest.approx(2160.0)


def test_highest_conviction_is_funded_first():
    """The whole point: the budget must follow the score, not the dict order."""
    out = _allocate(_bt496659(), 1200.0, 6000.0, target_pct=0.12)
    assert "NXT" in out and out["NXT"]["buy_cash"] == pytest.approx(720.0)
    assert "RGEN" not in out and "VOYA" not in out


def test_budget_is_never_exceeded():
    for cap in (500.0, 1200.0, 2280.0, 3000.0):
        out = _allocate(_bt496659(), cap, 6000.0, target_pct=0.12)
        assert sum(v["buy_cash"] for v in out.values()) <= cap + 0.01


def test_leftover_below_the_floor_is_dropped_not_shipped_undersized():
    sizes = {"A": {"buy_cash": 700.0, "raw_net_score": 2.0},
             "B": {"buy_cash": 700.0, "raw_net_score": 1.0}}
    out = _allocate(sizes, 740.0, 6000.0, target_pct=0.12)
    assert set(out) == {"A"}
    assert out["A"]["buy_cash"] == pytest.approx(720.0)


def test_target_zero_keeps_each_names_requested_size():
    out = _allocate(_bt496659(), 1200.0, 6000.0, target_pct=0.0)
    assert all(v["buy_cash"] == pytest.approx(561.4) for v in out.values())
    assert len(out) == 2


def test_under_budget_is_untouched():
    sizes = _bt496659()
    assert _allocate(sizes, 99999.0, 6000.0, target_pct=0.12) == sizes


def test_held_names_are_not_capped():
    """Adds to existing positions are outside this cap by design."""
    sizes = _bt496659()
    out = _allocate(sizes, 1200.0, 6000.0, target_pct=0.12, held=frozenset({"NXT"}))
    assert out["NXT"]["buy_cash"] == pytest.approx(561.4)


def test_ties_are_deterministic_not_dict_order():
    a = {"ZZZ": {"buy_cash": 500.0, "raw_net_score": 1.0},
         "AAA": {"buy_cash": 500.0, "raw_net_score": 1.0}}
    b = {"AAA": {"buy_cash": 500.0, "raw_net_score": 1.0},
         "ZZZ": {"buy_cash": 500.0, "raw_net_score": 1.0}}
    assert set(_allocate(a, 600.0, 6000.0)) == set(_allocate(b, 600.0, 6000.0))


def test_concentration_beats_scaling_on_the_objectives_own_arithmetic():
    """Four names at ~10% capturing half of a 60% move is the target.

    Uniform scaling cannot get there from a 38% satellite; concentration can.
    """
    scaled = _allocate(_bt496659(), 2280.0, 6000.0, concentrate=False)
    conc = _allocate(_bt496659(), 2280.0, 6000.0, target_pct=0.12)
    assert max(v["buy_cash"] for v in scaled.values()) / 6000.0 < 0.10
    assert min(v["buy_cash"] for v in conc.values()) / 6000.0 >= 0.10


# ── bt 865585: a funded slot the broker was always going to refuse ───────────


def _allocate_exec(sizes, cap, nav, target_pct=0.0, price_floor=8.0, prices=None):
    """Concentrate, but skip names that fail the broker's execution gates."""
    prices = prices or {}
    cands = []
    for sym, hint in sizes.items():
        cash = float(hint.get("buy_cash", 0.0) or 0.0)
        if cash > 0:
            cands.append((float(hint.get("raw_net_score", 0.0) or 0.0), cash, sym))
    if sum(c[1] for c in cands) <= cap:
        return dict(sizes)
    target = nav * target_pct if target_pct > 0 else 0.0
    floor = target if target > 0 else MIN_POS
    cands.sort(key=lambda t: (-t[0], -t[1], t[2]))
    out, left = dict(sizes), cap
    for _s, cash, sym in cands:
        if prices.get(sym, 999.0) < price_floor:
            out.pop(sym, None)
            continue
        take = min(max(cash, target) if target > 0 else cash, left)
        if take < floor or take < MIN_POS:
            out.pop(sym, None)
            continue
        out[sym] = {**out[sym], "buy_cash": round(take, 2)}
        left -= take
    return out


def test_sub_floor_name_does_not_consume_a_slot():
    """bt 865585: RIG took a full $720 (12% of NAV) and was then blocked by
    'Nexus execution price floor: RIG at $4.14 is below $8.00'."""
    sizes = {
        "NXT": {"buy_cash": 561.4, "raw_net_score": 1.8},
        "RIG": {"buy_cash": 561.4, "raw_net_score": 1.3},
        "CPER": {"buy_cash": 561.4, "raw_net_score": 1.2},
        "GDX": {"buy_cash": 561.4, "raw_net_score": 1.1},
        "WDC": {"buy_cash": 561.4, "raw_net_score": 1.0},
    }
    prices = {"NXT": 91.36, "RIG": 4.14, "CPER": 35.08,
              "GDX": 87.27, "WDC": 181.55}

    out = _allocate_exec(sizes, 2280.0, 6000.0, target_pct=0.12, prices=prices)

    assert "RIG" not in out
    # the freed slot goes to the next name down, not back to the core
    assert set(out) == {"NXT", "CPER", "GDX"}
    assert all(v["buy_cash"] == pytest.approx(720.0) for v in out.values())
    # satellite deploys its full design share instead of stranding 12% of NAV
    assert sum(v["buy_cash"] for v in out.values()) / 6000.0 == pytest.approx(0.36)


def test_without_the_check_the_satellite_under_deploys():
    """What 865585 actually did: 2 names filled, core absorbed the slack."""
    sizes = {
        "NXT": {"buy_cash": 561.4, "raw_net_score": 1.8},
        "RIG": {"buy_cash": 561.4, "raw_net_score": 1.3},
        "CPER": {"buy_cash": 561.4, "raw_net_score": 1.2},
        "GDX": {"buy_cash": 561.4, "raw_net_score": 1.1},
        "WDC": {"buy_cash": 561.4, "raw_net_score": 1.0},
    }
    funded = _allocate(sizes, 2280.0, 6000.0, target_pct=0.12)
    filled = {k: v for k, v in funded.items() if k != "RIG"}  # RIG refused
    assert sum(v["buy_cash"] for v in filled.values()) / 6000.0 == pytest.approx(0.24)
