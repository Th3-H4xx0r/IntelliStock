"""BFQ conviction target-weight sizing — `bfq_conviction_target_weight_pct`.

DEFECT (bt 571147, 2026-01-16, verbatim from `backtests/571147_audit.log`):

    V28 BFQ DRAIN ENTRY: queue_size=60 headroom=7 cash=$770
        priority_budget=$385 standard_budget=$385 min_pos=$100
    Backfill queue BUY: GLUE (queued 3 bars, alloc=$193, score=1.000 HIGH-CONV)
    Backfill queue BUY: SNDK (queued 6 bars, alloc=$100, score=1.700 HIGH-CONV)
    Backfill queue BUY: BTC  (queued 1 bars, alloc=$193, score=1.300)
    Backfill queue BUY: CMPX (queued 9 bars, alloc=$100, score=0.000)
    V28 BFQ ALLOC=0: MDB ... priority_budget=$93 standard_budget=$93 min_pos=$100

The highest-conviction name in a 60-deep queue was funded at $100 — 1.6% of a
$6,421 book — because the pool is halved three times (residual -> 50/50
priority/standard split -> `available * 0.5`) and, once `available` is under
`2 * min_required`, `priority_min_position_size` ($100, a BROKER MINIMUM)
becomes the entire position size. Nothing in the path ever measures the
allocation against NAV, so it can never be the 14% clip the concentrate
allocator uses for the same name on the same bar.

These tests pin the CURRENT (key-absent) arithmetic against five real drain
bars from four backtests, then assert the fix on the same bars.
"""

import os
import sys

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import pytest  # noqa: E402

from strategies.graph_nexus_analysis import (  # noqa: E402
    _plan_backfill_buy_allocation,
)

BASE_CFG = {
    "priority_min_position_size": 100.0,
    "priority_budget_can_bypass_regular_min": True,
}


def _item(ticker, queued_score, source="direct", propagation=False):
    return {
        "ticker": ticker,
        "raw_net_score": queued_score,
        "signal_source": source,
        "is_propagation_expansion": propagation,
        "is_watchlist_priority": False,
    }


def _replay_bar(cash, drains, config, *, nav=0.0, headroom=9, min_pos=100.0):
    """Mirror the drain loop in `run_once`: split the pool 50/50, then size and
    deduct in queue order exactly as gna does."""
    priority = cash * 0.50
    standard = cash - priority
    result = {}
    for ticker, queued, live, source, prop in drains:
        alloc, key = _plan_backfill_buy_allocation(
            _item(ticker, queued, source, prop),
            live,
            priority,
            standard,
            min_pos,
            headroom,
            config,
            portfolio_total=nav,
        )
        if alloc > 0.0:
            if key == "priority":
                priority = max(0.0, priority - alloc)
            else:
                standard = max(0.0, standard - alloc)
        result[ticker] = (round(alloc), key if alloc > 0.0 else "none")
    return result, round(priority), round(standard)


# (ticker, queued_score, live_score, signal_source, is_propagation_expansion)
BAR_571147_0116 = [
    ("GLUE", 2.000, 1.000, "direct", False),
    ("SNDK", 1.900, 1.700, "direct", False),
    ("BTC", 1.800, 1.300, "direct", False),
    ("CMPX", 1.800, 0.000, "direct", False),
    ("MDB", 1.800, 0.000, "direct", False),
]
BAR_915207_0109 = [
    ("SNDK", 1.900, 1.700, "direct", False),
    ("SBLK", 1.300, 1.300, "propagation_expansion", True),
    ("UBER", 1.300, 1.300, "direct", False),
    ("RVLV", 1.300, 1.300, "direct", False),
]
BAR_915207_0120 = [
    ("NUVB", 1.800, 0.000, "propagation_expansion", True),
    ("RVMD", 1.700, 1.700, "direct", False),
    ("SNDK", 1.700, 1.700, "direct", False),
    ("BRKR", 1.700, 0.000, "direct", False),
]
BAR_820236_0113 = [
    ("LLY", 1.750, 1.706, "direct", False),
    ("SNDK", 1.800, 1.700, "direct", False),
]
BAR_383778_0121 = [
    ("TERN", 1.800, 0.000, "propagation_expansion", True),
    ("INTC", 1.502, 1.502, "direct", False),
]


# ── 1. the key is absent: reproduce the shipped logs to the dollar ──────────
@pytest.mark.parametrize(
    "label,cash,nav,drains,expected,expected_pools",
    [
        # backtests/571147_audit.log:12743-12747 + the ALLOC=0 block after it
        ("571147 2026-01-16", 770.0, 6421.0, BAR_571147_0116,
         {"GLUE": (192, "priority"), "SNDK": (100, "priority"),
          "BTC": (192, "standard"), "CMPX": (100, "standard"),
          "MDB": (0, "none")}, (92, 92)),
        # backtests/915207_inv.log:7804-7807
        ("915207 2026-01-09", 741.0, 6000.0, BAR_915207_0109,
         {"SNDK": (185, "priority"), "SBLK": (100, "priority"),
          "UBER": (185, "standard"), "RVLV": (100, "standard")}, (85, 85)),
        # backtests/915207_inv.log:10680-10683
        ("915207 2026-01-20", 751.0, 6000.0, BAR_915207_0120,
         {"NUVB": (188, "priority"), "RVMD": (100, "priority"),
          "SNDK": (188, "standard"), "BRKR": (100, "standard")}, (88, 88)),
        # backtests/820236_20260808-142050Z.log:9232-9233
        ("820236 2026-01-13", 252.0, 6000.0, BAR_820236_0113,
         {"LLY": (100, "priority"), "SNDK": (100, "standard")}, (26, 26)),
        # backtests/383778_inv.log:8687-8688 (OOS bull window)
        ("383778 OOS", 241.0, 6000.0, BAR_383778_0121,
         {"TERN": (100, "priority"), "INTC": (100, "standard")}, (20, 20)),
    ],
)
def test_key_absent_reproduces_shipped_allocations(
    label, cash, nav, drains, expected, expected_pools
):
    """Regression guard: with the key absent the arithmetic is byte-identical
    to the four backtests these numbers were read out of."""
    got, priority_left, standard_left = _replay_bar(cash, drains, BASE_CFG, nav=nav)
    assert got == expected, label
    assert (priority_left, standard_left) == expected_pools, label


def test_key_absent_is_identical_with_and_without_portfolio_total():
    """`portfolio_total` is inert unless the key is set, so wiring it into the
    drain call site cannot move an existing run."""
    for cash, drains in (
        (770.0, BAR_571147_0116),
        (741.0, BAR_915207_0109),
        (252.0, BAR_820236_0113),
    ):
        blind, _, _ = _replay_bar(cash, drains, BASE_CFG, nav=0.0)
        sighted, _, _ = _replay_bar(cash, drains, BASE_CFG, nav=6421.0)
        assert blind == sighted
    # an explicit 0.0 pct is also off
    zero_cfg = dict(BASE_CFG, bfq_conviction_target_weight_pct=0.0)
    off, _, _ = _replay_bar(770.0, BAR_571147_0116, BASE_CFG, nav=6421.0)
    zero, _, _ = _replay_bar(770.0, BAR_571147_0116, zero_cfg, nav=6421.0)
    assert off == zero


# ── 2. the defect itself ───────────────────────────────────────────────────
def test_sndk_is_not_funded_at_the_broker_minimum():
    """bt 571147 2026-01-16: SNDK, raw 1.700, the top live-conviction name in a
    60-deep queue, was sized $100 = 1.6% of a $6,421 book while $192 sat unused
    in its own pool. FAILS without the fix (alloc == 100)."""
    cfg = dict(BASE_CFG, bfq_conviction_target_weight_pct=0.14)
    got, _, _ = _replay_bar(770.0, BAR_571147_0116, cfg, nav=6421.0)
    alloc, key = got["SNDK"]
    assert key == "priority"
    assert alloc > 100, "SNDK still pinned to priority_min_position_size"
    # it takes what is left of its pool ($385 - GLUE's $192.5), not a floor
    assert alloc == 192
    # and it is no longer a runt against NAV
    assert alloc / 6421.0 > 0.025


@pytest.mark.parametrize(
    "label,cash,nav,drains,ticker,was,now",
    [
        ("571147 2026-01-16", 770.0, 6421.0, BAR_571147_0116, "SNDK", 100, 192),
        ("915207 2026-01-09", 741.0, 6000.0, BAR_915207_0109, "SNDK", 185, 370),
        ("915207 2026-01-20", 751.0, 6000.0, BAR_915207_0120, "SNDK", 188, 376),
        ("915207 2026-01-20", 751.0, 6000.0, BAR_915207_0120, "RVMD", 100, 188),
        ("820236 2026-01-13", 252.0, 6000.0, BAR_820236_0113, "SNDK", 100, 126),
        ("820236 2026-01-13", 252.0, 6000.0, BAR_820236_0113, "LLY", 100, 126),
        ("383778 OOS", 241.0, 6000.0, BAR_383778_0121, "INTC", 100, 120),
    ],
)
def test_conviction_names_grow_on_every_measured_window(
    label, cash, nav, drains, ticker, was, now
):
    """Same mechanism on 4 backtests / 3 regimes. Never smaller than today."""
    off, _, _ = _replay_bar(cash, drains, BASE_CFG, nav=nav)
    on, _, _ = _replay_bar(
        cash, drains, dict(BASE_CFG, bfq_conviction_target_weight_pct=0.14), nav=nav
    )
    assert off[ticker][0] == was, f"{label} {ticker} baseline drifted"
    assert on[ticker][0] == now, f"{label} {ticker}"
    assert on[ticker][0] >= off[ticker][0]


# ── 3. the bounds ──────────────────────────────────────────────────────────
def test_allocation_never_exceeds_the_pool_that_exists():
    """'Bounded by the budget that actually exists' — a 14% target on a $6,000
    book is $840, but a $126 pool may only pay $126."""
    cfg = dict(BASE_CFG, bfq_conviction_target_weight_pct=0.14)
    alloc, key = _plan_backfill_buy_allocation(
        _item("SNDK", 1.800), 1.700,
        126.0, 0.0, 100.0, 5, cfg, portfolio_total=6000.0,
    )
    assert key == "priority"
    assert alloc == pytest.approx(126.0)


def test_allocation_never_exceeds_the_target_weight():
    """A huge pool does not buy a huge position: 14% of $6,000 caps at $840."""
    cfg = dict(BASE_CFG, bfq_conviction_target_weight_pct=0.14)
    alloc, _ = _plan_backfill_buy_allocation(
        _item("SNDK", 1.800), 1.700,
        5000.0, 0.0, 100.0, 5, cfg, portfolio_total=6000.0,
    )
    assert alloc == pytest.approx(840.0)
    # the shipped rule would have taken half the pool instead
    was, _ = _plan_backfill_buy_allocation(
        _item("SNDK", 1.800), 1.700, 5000.0, 0.0, 100.0, 5, BASE_CFG,
    )
    assert was == pytest.approx(2500.0)


def test_pool_below_the_min_position_floor_still_refuses():
    """The fix must not invent sub-floor positions: $93/$93 pools (the exact
    571147 2026-01-16 tail state) stay ALLOC=0."""
    cfg = dict(BASE_CFG, bfq_conviction_target_weight_pct=0.14)
    alloc, _ = _plan_backfill_buy_allocation(
        _item("SNDK", 1.800), 1.700,
        92.5, 92.5, 100.0, 5, cfg, portfolio_total=6421.0,
    )
    assert alloc == 0.0


def test_zero_headroom_still_refuses():
    cfg = dict(BASE_CFG, bfq_conviction_target_weight_pct=0.14)
    alloc, key = _plan_backfill_buy_allocation(
        _item("SNDK", 1.800), 1.700, 4000.0, 4000.0, 100.0, 0, cfg,
        portfolio_total=6421.0,
    )
    assert (alloc, key) == (0.0, "none")


# ── 4. it gates on the LIVE score, not the stale queued one ────────────────
def test_decayed_names_do_not_get_the_target_weight():
    """bt 915207 2026-01-20 funded `NUVB (alloc=$188, score=0.000 HIGH-CONV)`
    ahead of `RVMD (alloc=$100, score=1.700)` — the drain order is keyed on the
    stale queued score. A name whose live score has decayed to 0.000 must not
    be able to claim a 14% slot off a queued 1.800."""
    cfg = dict(BASE_CFG, bfq_conviction_target_weight_pct=0.14)
    off, _, _ = _replay_bar(751.0, BAR_915207_0120, BASE_CFG, nav=6000.0)
    on, _, _ = _replay_bar(751.0, BAR_915207_0120, cfg, nav=6000.0)
    assert off["NUVB"] == on["NUVB"] == (188, "priority")
    # same shape in the OOS bull window: TERN queued 1.800, live 0.000
    tern_off, _, _ = _replay_bar(241.0, BAR_383778_0121, BASE_CFG, nav=6000.0)
    tern_on, _, _ = _replay_bar(241.0, BAR_383778_0121, cfg, nav=6000.0)
    assert tern_off["TERN"] == tern_on["TERN"] == (100, "priority")


def test_below_threshold_names_are_untouched():
    """GLUE (live 1.000) / BTC (live 1.300) / CMPX (live 0.000) keep the
    shipped `available * 0.5` sizing."""
    cfg = dict(BASE_CFG, bfq_conviction_target_weight_pct=0.14)
    off, _, _ = _replay_bar(770.0, BAR_571147_0116, BASE_CFG, nav=6421.0)
    on, _, _ = _replay_bar(770.0, BAR_571147_0116, cfg, nav=6421.0)
    for sym in ("GLUE", "BTC", "CMPX"):
        assert off[sym] == on[sym], sym


def test_threshold_is_configurable_and_defaults_to_the_high_conviction_bar():
    cfg = dict(BASE_CFG, bfq_conviction_target_weight_pct=0.14)
    # 1.499 < the 1.5 default -> shipped rule (half of $400)
    low, _ = _plan_backfill_buy_allocation(
        _item("X", 1.499), 1.499, 400.0, 0.0, 100.0, 5, cfg, portfolio_total=6000.0)
    assert low == pytest.approx(200.0)
    # 1.5 -> target weight, pool-bounded
    hi, _ = _plan_backfill_buy_allocation(
        _item("X", 1.500), 1.500, 400.0, 0.0, 100.0, 5, cfg, portfolio_total=6000.0)
    assert hi == pytest.approx(400.0)
    # the bar itself moves with nexus_high_conviction_threshold
    strict = dict(cfg, nexus_high_conviction_threshold=1.8)
    assert _plan_backfill_buy_allocation(
        _item("X", 1.700), 1.700, 400.0, 0.0, 100.0, 5, strict,
        portfolio_total=6000.0)[0] == pytest.approx(200.0)
    # and an explicit override wins over it
    override = dict(strict, bfq_conviction_target_min_score=1.6)
    assert _plan_backfill_buy_allocation(
        _item("X", 1.700), 1.700, 400.0, 0.0, 100.0, 5, override,
        portfolio_total=6000.0)[0] == pytest.approx(400.0)


def test_no_portfolio_total_falls_back_to_the_shipped_rule():
    """A bar with no resolvable NAV must not silently size on nothing."""
    cfg = dict(BASE_CFG, bfq_conviction_target_weight_pct=0.14)
    alloc, _ = _plan_backfill_buy_allocation(
        _item("SNDK", 1.800), 1.700, 400.0, 0.0, 100.0, 5, cfg, portfolio_total=0.0)
    assert alloc == pytest.approx(200.0)


# ── 5. differential proof of the default-OFF claim ─────────────────────────
def _shipped_reference(queue_item, current_score, priority_budget, standard_budget,
                       min_position_size, headroom, config):
    """Verbatim copy of the pre-fix `_plan_backfill_buy_allocation` body
    (graph_nexus_analysis.py @ cd630af). Any divergence from the live function
    with the key absent is a behaviour change and must fail here."""
    config = config or {}
    if headroom <= 0 or min_position_size <= 0.0:
        return 0.0, "none"
    queued_score = float(queue_item.get("raw_net_score", 0.0) or 0.0)
    signal_source = str(queue_item.get("signal_source") or "").strip().lower()
    is_priority_entry = (
        bool(queue_item.get("is_watchlist_priority"))
        or bool(queue_item.get("is_propagation_expansion"))
        or signal_source in {"watchlist_priority", "propagation_expansion"}
    )
    priority_min_position_size = max(
        0.0, float(config.get("priority_min_position_size", 100.0) or 100.0))
    priority_budget_can_bypass = bool(
        config.get("priority_budget_can_bypass_regular_min", True))
    is_priority_size_override = (
        is_priority_entry
        and priority_budget_can_bypass
        and max(float(current_score or 0.0), queued_score) >= 0.50
    )
    min_required = (priority_min_position_size if is_priority_size_override
                    else min_position_size)
    is_high_conviction = (current_score >= 1.5 or queued_score >= 1.5
                          or is_priority_entry)
    budget_key = "priority" if is_high_conviction and priority_budget > 0.0 else "standard"
    available = max(0.0, priority_budget if budget_key == "priority" else standard_budget)
    if available < min_required:
        alt_key = "standard" if budget_key == "priority" else "priority"
        alt_available = max(
            0.0, standard_budget if alt_key == "standard" else priority_budget)
        if alt_available < min_required:
            return 0.0, budget_key
        budget_key = alt_key
        available = alt_available
    allocation = min(available, max(min_required, available * 0.5))
    return max(0.0, allocation), budget_key


def _grid():
    import random
    rng = random.Random(7)
    srcs = ["direct", "propagation_expansion", "watchlist_priority",
            "momentum_watchlist", ""]
    for _ in range(4000):
        yield (
            {
                "ticker": "T",
                "raw_net_score": round(rng.uniform(-1.0, 3.0), 3),
                "signal_source": rng.choice(srcs),
                "is_propagation_expansion": rng.random() < 0.3,
                "is_watchlist_priority": rng.random() < 0.1,
            },
            round(rng.uniform(-1.0, 3.0), 3),
            rng.choice([0.0, rng.uniform(0, 5000)]),
            rng.choice([0.0, rng.uniform(0, 5000)]),
            rng.choice([0.0, 50.0, 100.0, 250.0]),
            rng.choice([0, 1, 3, 9]),
            {
                "priority_min_position_size": rng.choice([0.0, 100.0, 400.0]),
                "priority_budget_can_bypass_regular_min": rng.random() < 0.8,
                "nexus_high_conviction_threshold": rng.choice([1.0, 1.5, 1.8]),
            },
            rng.choice([0.0, 6000.0, 6421.0, 50000.0]),
        )


def test_key_absent_matches_the_shipped_body_on_a_random_grid():
    for item, live, pri, std, minpos, hr, cfg, nav in _grid():
        assert _plan_backfill_buy_allocation(
            item, live, pri, std, minpos, hr, cfg, portfolio_total=nav
        ) == _shipped_reference(item, live, pri, std, minpos, hr, cfg), (
            item, live, pri, std, minpos, hr, cfg, nav)


def test_key_on_below_threshold_matches_the_shipped_body():
    for item, live, pri, std, minpos, hr, cfg, nav in _grid():
        bar = float(cfg["nexus_high_conviction_threshold"])
        if live >= bar:
            continue
        on = dict(cfg, bfq_conviction_target_weight_pct=0.14)
        assert _plan_backfill_buy_allocation(
            item, live, pri, std, minpos, hr, on, portfolio_total=nav
        ) == _shipped_reference(item, live, pri, std, minpos, hr, cfg)


def _min_required(queue_item, current_score, min_position_size, config):
    """The effective floor the shipped code applies (priority override or not)."""
    queued = float(queue_item.get("raw_net_score", 0.0) or 0.0)
    src = str(queue_item.get("signal_source") or "").strip().lower()
    is_priority = (
        bool(queue_item.get("is_watchlist_priority"))
        or bool(queue_item.get("is_propagation_expansion"))
        or src in {"watchlist_priority", "propagation_expansion"}
    )
    override = (
        is_priority
        and bool(config.get("priority_budget_can_bypass_regular_min", True))
        and max(float(current_score or 0.0), queued) >= 0.50
    )
    return (max(0.0, float(config.get("priority_min_position_size", 100.0) or 100.0))
            if override else min_position_size)


def test_key_on_replaces_half_the_pool_with_a_real_target_weight():
    """`min(available, max(min_required, available*0.5))` becomes
    `min(available, max(min_required, pct*NAV))` — the size stops tracking the
    size of a residual and starts tracking the book. That is a floor for the
    starved case AND a ceiling for the fat-pool case: the shipped rule will pay
    38% of NAV out of a $4,562 pool, which is not a position, it is the book."""
    raised = capped = 0
    for item, live, pri, std, minpos, hr, cfg, nav in _grid():
        if nav <= 0:
            continue
        on = dict(cfg, bfq_conviction_target_weight_pct=0.14)
        new, key = _plan_backfill_buy_allocation(
            item, live, pri, std, minpos, hr, on, portfolio_total=nav)
        old, old_key = _shipped_reference(item, live, pri, std, minpos, hr, cfg)
        if live < float(cfg["nexus_high_conviction_threshold"]) or old == 0.0:
            assert (new, key) == (old, old_key)
            continue
        available = max(0.0, pri if key == "priority" else std)
        min_required = _min_required(item, live, minpos, cfg)
        assert new == pytest.approx(
            min(available, max(min_required, 0.14 * nav)), abs=1e-6)
        assert new <= available + 1e-9
        if new > old:
            raised += 1
        elif new < old:
            capped += 1
    # both directions are exercised by the grid
    assert raised > 0 and capped > 0


def test_target_weight_is_a_floor_for_starved_pools_and_a_ceiling_for_fat_ones():
    cfg = dict(BASE_CFG, bfq_conviction_target_weight_pct=0.14)
    nav = 6000.0                       # 14% == $840
    # starved: shipped rule pays the $100 broker minimum, fix pays the pool
    assert _plan_backfill_buy_allocation(
        _item("A", 1.8), 1.8, 190.0, 0.0, 100.0, 5, BASE_CFG)[0] == pytest.approx(100.0)
    assert _plan_backfill_buy_allocation(
        _item("A", 1.8), 1.8, 190.0, 0.0, 100.0, 5, cfg,
        portfolio_total=nav)[0] == pytest.approx(190.0)
    # fat: shipped rule pays 38% of NAV, fix caps at the 14% target
    assert _plan_backfill_buy_allocation(
        _item("A", 1.8), 1.8, 4562.0, 0.0, 100.0, 5, BASE_CFG)[0] == pytest.approx(2281.0)
    assert _plan_backfill_buy_allocation(
        _item("A", 1.8), 1.8, 4562.0, 0.0, 100.0, 5, cfg,
        portfolio_total=nav)[0] == pytest.approx(840.0)
