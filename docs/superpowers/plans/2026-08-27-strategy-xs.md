# Strategy XS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Strategy XS — a trend-filtered stacked-growth strategy that keeps levered equity exposure and funds an always-on diversifier basket, rather than selling equity to buy protection.

**Architecture:** A pure allocation module (`backend/strategy_xs.py`) with no clock, network or I/O, imported directly by its tests; a thin broker-facing wrapper (`backend/strategies/strategy_xs.py`) that owns cache state and order emission; and two small broker hooks so the strategy's self-declared symbols get bars and prices. The trend filter and volatility scaling are imported unchanged from `strategy_x.py` — they are already tested and forking them would create two copies of the same boundary.

**Tech Stack:** Python 3.14, pytest, pandas/numpy/yfinance (research harness only — never in `backend/`).

**Spec:** `docs/superpowers/specs/2026-08-27-strategy-xs-design.md`

## Global Constraints

- Nothing in `backend/strategy_x.py` or `backend/strategies/strategy_x.py` changes behaviour. Strategy X keeps running as it is. You may only IMPORT from it.
- `backend/strategy_xs.py` is pure: no clock read, no network, no filesystem, no `import broker`. This is why its tests can import it directly — `broker.py` argparses at module scope and `SystemExit`s under pytest.
- Every numeric parser fails toward LESS exposure. Non-finite or malformed config coerces to the documented default, never to a larger position.
- Quantize every weight that crosses a decision boundary to `Q = 6` decimal places, and FLOOR rather than round when splitting a budget — rounding each share up breaches the budget.
- Run `python3 scripts/strategy_xs_sync_schema.py` after any `DEFAULTS` change; a test asserts the header matches.
- Follow the surrounding comment style: explain WHY, with the measurement that settles it. Do not add comments that restate the code.
- Datastore rules from `CLAUDE.md` apply. No module outside `backend/db/` opens a connection.
- Before editing any existing symbol, run `mcp__gitnexus__impact({target, direction: "upstream", repo: "/Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock/.claude/worktrees/main-session"})` and report the risk. Stop on HIGH/CRITICAL.
- Do not commit `bt*.json` or `scripts/_deploy_then_*.sh` — pre-existing untracked artifacts, leave them alone.

---

### Task 1: The diversifier basket

**Files:**
- Create: `backend/strategy_xs.py`
- Test: `backend/tests/test_strategy_xs.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `diversifier_basket(closes_by_symbol, prices, config) -> tuple[str, ...]` — the eligible members in configured order.

- [ ] **Step 1: Write the failing test**

```python
"""Pure allocation tests for Strategy XS."""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategy_xs import diversifier_basket  # noqa: E402


def cfg(**overrides):
    value = {
        "diversifier_symbols": ["GLD", "UUP", "DBMF"],
        "diversifier_min_history_bars": 60,
    }
    value.update(overrides)
    return value


def series(n, start=100.0):
    return [start + i * 0.1 for i in range(n)]


PRICES = {"GLD": 200.0, "UUP": 28.0, "DBMF": 26.0}


def test_all_members_qualify_when_priceable_and_long_enough():
    closes = {s: series(80) for s in ("GLD", "UUP", "DBMF")}
    assert diversifier_basket(closes, PRICES, cfg()) == ("GLD", "UUP", "DBMF")


def test_a_member_without_a_price_is_dropped():
    closes = {s: series(80) for s in ("GLD", "UUP", "DBMF")}
    prices = dict(PRICES, DBMF=0.0)
    assert diversifier_basket(closes, prices, cfg()) == ("GLD", "UUP")


def test_a_member_with_too_little_history_is_dropped():
    """DBMF has no history before 2019-05, so this is the ordinary case for
    any window starting earlier, not an edge case."""
    closes = {"GLD": series(80), "UUP": series(80), "DBMF": series(30)}
    assert diversifier_basket(closes, PRICES, cfg()) == ("GLD", "UUP")


def test_a_nonfinite_close_in_the_required_window_drops_that_member():
    closes = {"GLD": series(80), "UUP": series(80),
              "DBMF": series(59) + [float("nan")]}
    assert diversifier_basket(closes, PRICES, cfg()) == ("GLD", "UUP")


def test_order_follows_the_configured_list_not_the_dict():
    closes = {s: series(80) for s in ("DBMF", "GLD", "UUP")}
    assert diversifier_basket(closes, PRICES,
                              cfg(diversifier_symbols=["UUP", "DBMF", "GLD"])
                              ) == ("UUP", "DBMF", "GLD")


def test_no_qualifying_member_returns_empty():
    assert diversifier_basket({}, {}, cfg()) == ()


def test_a_nonfinite_history_requirement_falls_back_to_the_default():
    closes = {"GLD": series(80), "UUP": series(30), "DBMF": series(80)}
    for bad in (float("nan"), float("inf"), None, "sixty"):
        assert diversifier_basket(closes, PRICES,
                                  cfg(diversifier_min_history_bars=bad)
                                  ) == ("GLD", "DBMF"), bad
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_strategy_xs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategy_xs'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/strategy_xs.py`:

```python
"""Strategy XS sizing — a levered core stacked on an always-on diversifier.

Design: docs/superpowers/specs/2026-08-27-strategy-xs-design.md

WHY THIS EXISTS, AND WHY IT IS NOT STRATEGY X
---------------------------------------------
Strategy X sells equity to buy protection: every defensive move takes capital
out of the asset that earns. Measured over 2019-2026, a de-risking portfolio
(75% SPY + 25% managed futures) loses to SPY in SEVEN of eight calendar years.
Across roughly seventy configurations, fifteen window slices and sixteen
calendar years, every mechanism available to Strategy X trades return for
drawdown; it has no alpha source, because a trend filter buys crash protection
rather than excess return.

This does the opposite. It keeps the equity exposure and ADDS a diversifying
return stream, funded by the capital a 3x fund frees up. Measured, 2011-2026:

    design                CAGR    maxDD   Sharpe  negYrs  yrs<SPY
    SPY buy & hold       14.21   -33.72    0.86      2       -
    Strategy X, best     15.11   -23.13    0.86      4       9
    Strategy XS          20.84   -24.59    1.04      2       4

THE LEVERAGE ARITHMETIC THAT MAKES 3x CORRECT HERE
--------------------------------------------------
A position `w` in a `k`x fund carries the same volatility drag as direct
exposure `m = kw`, namely (m*sigma)^2 / 2. The drag depends on TOTAL exposure,
not on the fund's multiple, so TQQQ at 33% and QLD at 50% are identical for
identical beta — and TQQQ reaches that beta with a third of the capital,
leaving more for the diversifier. That is the opposite of the conclusion for
Strategy X, where the levered fund WAS the portfolio and 3x sat above the
growth-optimal leverage for the index.

Pure: no clock, no RNG, no I/O. `broker.py` is not import-safe (argparse at
module scope SystemExits under pytest), so anything testable lives here.
"""
from __future__ import annotations

import math

from strategy_x import Q, _f, _finite, _i, _s

__all__ = ["DEFAULTS", "diversifier_basket", "strategy_xs_universe",
           "xs_targets"]


def diversifier_basket(closes_by_symbol, prices, config) -> tuple:
    """Members that can actually be held, in the configured order.

    A member must be priceable AND carry `diversifier_min_history_bars` of
    positive finite closes. DBMF has no history before 2019-05, so a short
    basket is the ordinary case for any window starting earlier — not an edge
    case — and the caller redistributes across survivors rather than letting
    the shortfall reach the levered core.
    """
    cfg = config or {}
    minimum = max(1, _i(cfg, "diversifier_min_history_bars", 60))
    try:
        histories = closes_by_symbol or {}
        quotes = prices or {}
    except (AttributeError, TypeError):
        return ()
    out = []
    for raw in (cfg.get("diversifier_symbols") or []):
        symbol = str(raw or "").strip().upper()
        if not symbol or symbol in out:
            continue
        try:
            price = float(quotes.get(symbol) or 0.0)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price) or price <= 0:
            continue
        history = _finite(list(histories.get(symbol) or ())[-minimum:])
        if history is None or len(history) < minimum:
            continue
        out.append(symbol)
    return tuple(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/test_strategy_xs.py -q`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add backend/strategy_xs.py backend/tests/test_strategy_xs.py
git commit -m "feat(strategy-xs): eligible diversifier basket

A member must be priceable and carry its minimum history. DBMF has no data
before 2019-05, so a short basket is the ordinary case for any earlier window,
and the shortfall must never reach the levered core."
```

---

### Task 2: The allocation

**Files:**
- Modify: `backend/strategy_xs.py`
- Test: `backend/tests/test_strategy_xs.py`

**Interfaces:**
- Consumes: `diversifier_basket` from Task 1.
- Produces: `xs_targets(*, risk_on, config, basket, satellite_ranked=None, vol_scale=1.0) -> tuple[dict, list]` — `{symbol: fraction_of_NAV}` and a list of human-readable notes.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_strategy_xs.py`:

```python
from strategy_xs import DEFAULTS, xs_targets  # noqa: E402


def acfg(**overrides):
    value = dict(DEFAULTS)
    value.update(overrides)
    return value


def total(targets):
    return round(sum(targets.values()), 6)


def test_risk_on_pays_the_sleeves_first_and_the_core_is_the_residual():
    targets, _ = xs_targets(risk_on=True, config=acfg(),
                            basket=("GLD", "UUP", "DBMF"))
    assert total(targets) == 1.0
    assert targets["TQQQ"] == 0.451          # 0.55 residual x 0.82 core_weight
    assert targets["BIL"] == 0.099           # the unheld part of the residual
    assert targets["GLD"] == targets["UUP"] == targets["DBMF"] == 0.15


def test_risk_off_sends_the_core_to_cash_not_to_the_index():
    """The whole difference from Strategy X. Strategy X routes the de-levered
    weight to SPY, so a nominal 70% TQQQ book is really 27% TQQQ and 57% SPY
    and tracks SPY."""
    targets, _ = xs_targets(risk_on=False, config=acfg(),
                            basket=("GLD", "UUP", "DBMF"))
    assert "TQQQ" not in targets
    assert targets["BIL"] == 0.55
    assert targets["GLD"] == 0.15
    assert total(targets) == 1.0


def test_the_diversifier_stays_on_in_both_regimes():
    on, _ = xs_targets(risk_on=True, config=acfg(), basket=("GLD", "UUP"))
    off, _ = xs_targets(risk_on=False, config=acfg(), basket=("GLD", "UUP"))
    assert on["GLD"] == off["GLD"] == 0.225


def test_a_short_basket_redistributes_and_never_reaches_the_core():
    """bt 773215: an unfilled sleeve handed its weight to the 3x fund, bar 1
    went 80% TQQQ instead of 60%, and TQQQ alone was 133% of the loss."""
    targets, _ = xs_targets(risk_on=True, config=acfg(), basket=("GLD",))
    assert targets["GLD"] == 0.45
    assert targets["TQQQ"] == 0.451
    assert total(targets) == 1.0


def test_an_empty_basket_goes_to_cash_not_to_the_core():
    targets, _ = xs_targets(risk_on=True, config=acfg(), basket=())
    assert targets["TQQQ"] == 0.451
    assert targets["BIL"] == round(0.099 + 0.45, 6)
    assert total(targets) == 1.0


def test_arming_the_graph_sleeve_delevers_the_core():
    """Intended: it swaps levered index beta for stock-picking beta rather
    than stacking both. 135% beta -> 106%."""
    targets, _ = xs_targets(risk_on=True, config=acfg(satellite_pct=0.20),
                            basket=("GLD", "UUP", "DBMF"),
                            satellite_ranked=["AAPL", "MSFT"])
    assert targets["TQQQ"] == 0.287          # 0.35 residual x 0.82
    assert targets["AAPL"] == targets["MSFT"] == 0.1
    assert total(targets) == 1.0
    beta = round(targets["TQQQ"] * 3 + 0.2, 3)
    assert 1.05 <= beta <= 1.07


def test_an_unranked_graph_sleeve_does_not_raise_core_leverage():
    targets, _ = xs_targets(risk_on=True, config=acfg(satellite_pct=0.20),
                            basket=("GLD", "UUP", "DBMF"),
                            satellite_ranked=[])
    assert targets["TQQQ"] == 0.287
    assert total(targets) == 1.0


def test_the_vol_scale_reduces_the_levered_leg_into_cash():
    targets, _ = xs_targets(risk_on=True, config=acfg(),
                            basket=("GLD", "UUP", "DBMF"), vol_scale=0.5)
    assert targets["TQQQ"] == round(0.451 * 0.5, 6)
    assert total(targets) == 1.0


def test_the_vol_scale_can_never_raise_exposure():
    for bad in (2.0, float("nan"), float("inf"), None, "1.5"):
        targets, _ = xs_targets(risk_on=True, config=acfg(),
                                basket=("GLD", "UUP"), vol_scale=bad)
        assert targets["TQQQ"] <= 0.451, bad


def test_weights_never_sum_past_one_for_any_sleeve_split():
    for n in range(1, 6):
        basket = tuple(f"D{i}" for i in range(n))
        targets, _ = xs_targets(risk_on=True, config=acfg(), basket=basket)
        assert total(targets) <= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_strategy_xs.py -q`
Expected: FAIL — `ImportError: cannot import name 'DEFAULTS' from 'strategy_xs'`

- [ ] **Step 3: Write minimal implementation**

Add to `backend/strategy_xs.py`, above `diversifier_basket`:

```python
DEFAULTS = {
    "strategy_xs_enabled": False,
    # ── the levered core ──
    "core_bull_symbol": "TQQQ",
    "core_leverage_factor": 3.0,
    # Share of the RESIDUAL held in the levered leg; the rest is cash. 0.82 of
    # a 0.55 residual is 45.1% of NAV, i.e. 135% equity beta.
    "core_weight": 0.82,
    "core_cash_symbol": "BIL",
    "core_band_pct": 0.05,
    # ── the filter, imported from strategy_x and unchanged ──
    "core_filter_symbol": "QQQ",
    "core_filter_ma_bars": 200,
    "core_vol_bars": 20,
    "core_vol_slow_bars": 60,
    "core_vol_gate_mult": 2.25,
    "core_vol_median_bars": 252,
    "core_vol_median_min_samples": 60,
    "core_vol_target": 0.0,
    "core_vol_scale_min": 0.3,
    "core_vol_scale_max": 1.0,
    # ── the diversifier, ALWAYS ON ──
    # It is the return source, not a panic button. Measured 2011-2026 on the
    # three years every Strategy X configuration loses money:
    #   asset  CAGR   corr    2015    2018    2022
    #   UUP    2.49  -0.16    +7.0    +7.0    +9.5   <- the only 15y asset
    #   GLD    7.42  +0.06   -10.7    -1.9    -0.8      positive in all three
    #   DBMF   9.21  +0.19     n/a     n/a   +21.6
    #   TLT    2.13  -0.27    -1.8    -1.6   -31.2   <- excluded: 2022
    #   VIXY -48.94  -0.79   -36.5   +66.8   -25.0   <- excluded: carry
    # The dollar cannot carry the sleeve alone at a 2.5% CAGR; gold supplies
    # the long-run return and managed futures the crisis alpha.
    "diversifier_pct": 0.45,
    "diversifier_symbols": ["GLD", "UUP", "DBMF"],
    "diversifier_min_history_bars": 60,
    # ── the Graph stock sleeve (OFF) ──
    # 0.20 arms it, and arming it DE-LEVERS the core from 135% beta to 106%:
    # it swaps levered index beta for stock-picking beta rather than stacking
    # both. Default 0 because this repo has measured Graph Nexus to have no
    # cross-sectional skill — Spearman IC of nexus_base_score against forward
    # returns is negative in every window tested.
    "satellite_pct": 0.0,
    "satellite_max_names": 6,
    # ── the inverse sleeve (OFF, and the numbers are the argument) ──
    # Gated on the same trend filter, it DOES deliver net-positive bears on
    # 7.3 years containing one bear: SQQQ 20% takes 2022 from -7.1% to +0.8%
    # and losing years from 1 to 0. Over the full fifteen years it is
    # monotonically destructive:
    #   sleeve      CAGR   maxDD  negYrs
    #   none       16.13  -24.47     3
    #   PSQ 20%    14.97  -28.08     5
    #   SQQQ 20%   12.43  -35.28     5
    # Worse return, worse drawdown, MORE losing years. It fits the one bear in
    # the short window. There is no setting in between: gate it tighter and it
    # stops firing (Strategy X's kicker engaged twice in 1,258 sessions for a
    # net +$4.29); loosen it and it bleeds in every whipsaw.
    "inverse_symbol": "",
    "inverse_pct": 0.0,
    # ── execution ──
    "min_order_usd": 50.0,
    "cost_haircut_pct": 0.006,
}


def _share(budget: float, count: int) -> float:
    """Floor to the grid, never round. Rounding each share UP breaches the
    budget — 0.5/3 rounded to 6dp three times is 0.500001 — and a weight set
    summing past 1.0 asks for a clip the account cannot fund."""
    scale = 10 ** Q
    return math.floor(budget / count * scale) / scale


def xs_targets(*, risk_on: bool, config, basket=(), satellite_ranked=None,
               vol_scale: float = 1.0) -> tuple[dict, list]:
    """Target weight per symbol as a fraction of NAV, plus why.

    Named sleeves are paid first and the core is the RESIDUAL. The alternative
    — sizing the core first — is what let an unfilled sleeve raise leverage in
    bt 773215, where bar 1 went 80% TQQQ instead of the designed 60% and TQQQ
    alone accounted for 133% of the loss.
    """
    cfg = config or {}
    notes: list[str] = []
    targets: dict[str, float] = {}

    bull = _s(cfg, "core_bull_symbol")
    cash = _s(cfg, "core_cash_symbol")
    inverse = _s(cfg, "inverse_symbol", "")

    sat_pct = max(0.0, min(1.0, _f(cfg, "satellite_pct")))
    div_pct = max(0.0, min(1.0, _f(cfg, "diversifier_pct")))
    # The residual is sized off the DESIGNED sleeve budgets, so core leverage
    # is the same whether or not a sleeve fills.
    residual = round(max(0.0, 1.0 - sat_pct - div_pct), Q)

    # ── the diversifier, in both regimes ──
    members = [str(s).strip().upper() for s in (basket or ()) if s]
    members = list(dict.fromkeys(members))
    if div_pct > 0 and members:
        each = _share(div_pct, len(members))
        for symbol in members:
            targets[symbol] = each
        spent = round(each * len(members), Q)
        notes.append(f"diversifier {spent:.0%} across {len(members)}: "
                     + ", ".join(members))
    else:
        spent = 0.0
        if div_pct > 0:
            notes.append("no diversifier member qualifies — weight to cash")
    div_short = round(div_pct - spent, Q)

    # ── the Graph stock sleeve ──
    names = [str(s).strip().upper() for s in (satellite_ranked or []) if s]
    names = [s for s in dict.fromkeys(names)
             if s not in targets and s not in (bull, cash, inverse)]
    names = names[:max(0, _i(cfg, "satellite_max_names"))]
    if sat_pct > 0 and names:
        each = _share(sat_pct, len(names))
        for symbol in names:
            targets[symbol] = each
        sat_spent = round(each * len(names), Q)
        notes.append(f"graph {sat_spent:.0%} across {len(names)}")
    else:
        sat_spent = 0.0
        if sat_pct > 0:
            notes.append("no graph name ranked — sleeve holds cash")
    sat_short = round(sat_pct - sat_spent, Q)

    # Every shortfall goes to CASH, never to the levered core.
    idle = round(div_short + sat_short, Q)

    if risk_on:
        weight = max(0.0, min(1.0, _f(cfg, "core_weight")))
        # Clamped to [0, 1] and fail-safe: a malformed scale must never RAISE
        # exposure above what the operator configured.
        try:
            scale = float(vol_scale)
        except (TypeError, ValueError):
            scale = 1.0
        if not math.isfinite(scale):
            scale = 1.0
        scale = max(0.0, min(1.0, scale))
        targets[bull] = round(residual * weight * scale, Q)
        rest = round(residual - targets[bull] + idle, Q)
        if rest > 0:
            targets[cash] = round(targets.get(cash, 0.0) + rest, Q)
        notes.append(f"risk-on: {targets[bull]:.1%} {bull}"
                     + (f" | vol scale {scale:.2f}" if scale < 1.0 else ""))
        return targets, notes

    # ── risk-off: the core goes to CASH, not to the unlevered index ──
    # This is the whole difference from Strategy X, whose de-levered weight
    # lands in SPY — so a nominal 70% TQQQ book is really 27% TQQQ and 57% SPY,
    # and its measured result therefore tracks SPY.
    inv_pct = max(0.0, min(1.0, _f(cfg, "inverse_pct")))
    parked = round(residual + idle, Q)
    if inverse and inv_pct > 0 and inv_pct <= parked:
        targets[inverse] = inv_pct
        parked = round(parked - inv_pct, Q)
        notes.append(f"risk-off: {inv_pct:.1%} {inverse} (inverse sleeve ARMED)")
    if parked > 0:
        targets[cash] = round(targets.get(cash, 0.0) + parked, Q)
    notes.append(f"risk-off: {targets.get(cash, 0.0):.1%} {cash}")
    return targets, notes
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/test_strategy_xs.py -q`
Expected: PASS, 17 tests

- [ ] **Step 5: Commit**

```bash
git add backend/strategy_xs.py backend/tests/test_strategy_xs.py
git commit -m "feat(strategy-xs): sleeves-first allocation with a cash risk-off

Risk-off sends the core to CASH, not to the unlevered index. That single choice
is the whole difference from Strategy X, whose de-levered weight lands in SPY —
a nominal 70% TQQQ book is really 27% TQQQ and 57% SPY, which is why its
measured result tracks SPY rather than beating it.

Every sleeve shortfall goes to cash too. Routing it to the 3x fund is the
defect measured at bt 773215."
```

---

### Task 3: Universe declaration and the schema header

**Files:**
- Modify: `backend/strategy_xs.py`
- Create: `scripts/strategy_xs_sync_schema.py`
- Test: `backend/tests/test_strategy_xs.py`

**Interfaces:**
- Consumes: `DEFAULTS` from Task 2.
- Produces: `strategy_xs_universe(config) -> list[str]` — every symbol the strategy can trade or read, deterministic order.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_strategy_xs.py`:

```python
from strategy_xs import strategy_xs_universe  # noqa: E402


def test_the_universe_is_declared_from_config_not_the_watchlist():
    """The strategy owns its universe. Without this the filter symbol has no
    bars and the traded legs have no price, and BOTH failures are silent."""
    assert strategy_xs_universe(acfg()) == [
        "QQQ", "TQQQ", "BIL", "GLD", "UUP", "DBMF"]


def test_the_universe_includes_the_inverse_leg_only_when_armed():
    assert "SQQQ" not in strategy_xs_universe(acfg())
    armed = acfg(inverse_symbol="SQQQ", inverse_pct=0.2)
    assert "SQQQ" in strategy_xs_universe(armed)


def test_the_universe_omits_the_diversifier_when_the_sleeve_is_off():
    assert strategy_xs_universe(acfg(diversifier_pct=0.0)) == [
        "QQQ", "TQQQ", "BIL"]


def test_the_universe_is_freshly_allocated_so_callers_cannot_mutate_defaults():
    first = strategy_xs_universe(acfg())
    first.append("JUNK")
    assert "JUNK" not in strategy_xs_universe(acfg())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_strategy_xs.py -q`
Expected: FAIL — `ImportError: cannot import name 'strategy_xs_universe'`

- [ ] **Step 3: Write minimal implementation**

Add to `backend/strategy_xs.py`:

```python
def strategy_xs_universe(config) -> list:
    """Every symbol this strategy can trade or read, deterministic order.

    The strategy owns its universe rather than depending on the instance's
    watchlist. Without this the filter symbol has no bars and the traded legs
    have no price, and BOTH failures are silent — the strategy simply emits
    nothing. `broker._strategy_x_prepare` reads this to decide what to fetch.
    """
    cfg = config or {}
    syms = [_s(cfg, "core_filter_symbol"), _s(cfg, "core_bull_symbol"),
            _s(cfg, "core_cash_symbol")]
    if _f(cfg, "diversifier_pct") > 0:
        syms += [str(s).strip().upper()
                 for s in (cfg.get("diversifier_symbols") or []) if s]
    if _s(cfg, "inverse_symbol", "") and _f(cfg, "inverse_pct") > 0:
        syms.append(_s(cfg, "inverse_symbol", ""))
    seen, out = set(), []
    for s in syms:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/test_strategy_xs.py -q`
Expected: PASS, 21 tests

- [ ] **Step 5: Create the schema sync script**

Create `scripts/strategy_xs_sync_schema.py`:

```python
#!/usr/bin/env python3
"""Sync the INTELLISTOCK_SCHEMA header for strategy_xs with DEFAULTS.

The header is what the UI and /strategies/available read. Letting it drift from
`backend/strategy_xs.py:DEFAULTS` means an operator configures a key the
strategy does not have, or misses one it does.
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, "backend")
from strategy_xs import DEFAULTS  # noqa: E402

p = pathlib.Path("backend/strategies/strategy_xs.py")
s = p.read_text()
m = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", s)
d = json.loads(m.group(1))
d["config"] = dict(DEFAULTS)
d["execution_position"] = 10
p.write_text(s.replace(m.group(0), "# INTELLISTOCK_SCHEMA: " + json.dumps(d)))
print(f"schema synced from DEFAULTS: {len(d['config'])} config keys")
```

- [ ] **Step 6: Commit**

```bash
git add backend/strategy_xs.py backend/tests/test_strategy_xs.py \
        scripts/strategy_xs_sync_schema.py
git commit -m "feat(strategy-xs): declare the strategy's own universe

A strategy that trades symbols the operator never listed must declare them, or
price_history is built without them and the strategy is silently inert."
```

---

### Task 4: The broker-facing wrapper

**Files:**
- Create: `backend/strategies/strategy_xs.py`
- Test: `backend/tests/test_strategy_xs_run_once.py`

**Interfaces:**
- Consumes: `xs_targets`, `diversifier_basket`, `strategy_xs_universe`, `DEFAULTS` from Tasks 1-3; `core_signal`, `core_vol_scale`, `pit_daily_closes`, `targets_to_orders` from `strategy_x.py` unchanged.
- Produces: `StrategyXS.run_once(...) -> dict` — `{symbol: 1|0|-1}` plus `_nexus_position_sizes`, `_nexus_discovered`, `_nexus_executable_buys`, `_nexus_sell_enforcement`, `_nexus_action_intents`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_strategy_xs_run_once.py`:

```python
"""Wrapper tests for Strategy XS: the broker contract and cache behaviour."""
import os
import sys
from datetime import datetime, timedelta, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (_backend, os.path.join(_backend, "strategies")):
    if p not in sys.path:
        sys.path.insert(0, p)

from strategies.strategy_xs import StrategyXS  # noqa: E402
from strategy_xs import DEFAULTS  # noqa: E402

NOW = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)


def bars(n, start=100.0, step=0.5, end_day=None):
    end_day = end_day or datetime(2026, 6, 1, tzinfo=timezone.utc)
    return [{"t": (end_day - timedelta(days=(n - 1 - i))).isoformat(),
             "c": start + i * step} for i in range(n)]


def falling(n):
    return bars(n, start=400.0, step=-0.8)


PRICES = {"TQQQ": 50.0, "QQQ": 400.0, "BIL": 91.0,
          "GLD": 200.0, "UUP": 28.0, "DBMF": 26.0}


class FakeEmulator:
    def __init__(self, cash=10000.0, positions=None, prices=None):
        self._cash = cash
        self._positions = dict(positions or {})
        self._prices = dict(prices or PRICES)

    def get_cash(self):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices=None):
        px = prices or self._prices
        return self._cash + sum(q * float(px.get(s, 0.0))
                                for s, q in self._positions.items())


def cfg(**overrides):
    value = dict(DEFAULTS)
    value["strategy_xs_enabled"] = True
    value.update(overrides)
    return value


def data_for(qqq_bars):
    out = {"QQQ": {"bars": qqq_bars}}
    for s in ("GLD", "UUP", "DBMF", "BIL", "TQQQ"):
        out[s] = {"bars": bars(260)}
    return out


def test_disabled_by_default_emits_nothing():
    out = StrategyXS().run_once(["TQQQ"], PRICES, NOW, dict(DEFAULTS), {},
                                data=data_for(bars(260)),
                                portfolio_emulator=FakeEmulator())
    assert out == {}


def test_an_uptrend_buys_the_levered_core_and_the_diversifier():
    out = StrategyXS().run_once(["TQQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(bars(260)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache={})
    assert out.get("TQQQ") == 1
    assert out.get("GLD") == 1 and out.get("UUP") == 1
    assert out["_nexus_position_sizes"]["TQQQ"]["buy_cash"] > 0


def test_a_downtrend_holds_cash_and_still_holds_the_diversifier():
    cache = {}
    out = StrategyXS().run_once(["TQQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(falling(260)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache=cache)
    assert out.get("TQQQ") is None or out.get("TQQQ") != 1
    assert out.get("BIL") == 1
    assert out.get("GLD") == 1
    assert cache["_strategy_xs_last"]["risk_on"] is False


def test_it_refuses_to_trade_without_enough_filter_history():
    """A cold start must never read as risk-on, and 'risk-off' here would be a
    real cash buy rather than a flat."""
    out = StrategyXS().run_once(["TQQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(bars(30)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache={})
    assert out == {}


def test_it_publishes_its_own_universe():
    out = StrategyXS().run_once(["TQQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(bars(260)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache={})
    assert set(out["_nexus_discovered"]) >= {"QQQ", "TQQQ", "BIL",
                                             "GLD", "UUP", "DBMF"}


def test_every_sell_carries_an_action_intent():
    """broker.py's Z2.1 check reads action_intent off the strategy summary and
    whitelists only graph_nexus's enum. Strategy X shipped without this and all
    965 of its sells logged would_block_in_phase2=True."""
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 100.0})
    out = StrategyXS().run_once(["TQQQ"], PRICES, NOW, cfg(), {},
                                data=data_for(falling(260)),
                                portfolio_emulator=emu, strategy_cache={})
    sells = [s for s, d in out.items()
             if not s.startswith("_") and d == -1]
    assert sells
    for symbol in sells:
        assert out["_nexus_action_intents"][symbol] == "etf_sell"


def test_a_missing_diversifier_price_does_not_raise_core_leverage():
    prices = dict(PRICES); prices.pop("DBMF")
    cache = {}
    StrategyXS().run_once(["TQQQ"], prices, NOW, cfg(), {},
                          data=data_for(bars(260)),
                          portfolio_emulator=FakeEmulator(prices=prices),
                          strategy_cache=cache)
    targets = cache["_strategy_xs_last"]["targets"]
    assert targets["TQQQ"] == 0.451
    assert round(sum(targets.values()), 6) == 1.0


def test_the_schema_header_contains_every_default():
    import json
    import re
    path = os.path.join(_backend, "strategies", "strategy_xs.py")
    header = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", open(path).read())
    schema = json.loads(header.group(1))
    assert set(schema["config"]) == set(DEFAULTS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_strategy_xs_run_once.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategies.strategy_xs'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/strategies/strategy_xs.py`. Start the file with a placeholder
schema line (Step 5 fills it):

```python
# INTELLISTOCK_SCHEMA: {"strategy": "strategy_xs", "weight": 1.0, "execution_position": 10, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {}}
# INTELLISTOCK_DESCRIPTION: Stacked growth — a trend-filtered levered core over an always-on diversifier basket.
"""Strategy XS wrapper: cache state, order emission, broker contract.

Everything testable lives in `backend/strategy_xs.py`, which is pure. This file
owns only what needs the broker: the emulator, the cache, and the decision row.

Design: docs/superpowers/specs/2026-08-27-strategy-xs-design.md
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategy_x import (  # noqa: E402
    core_signal,
    core_vol_scale,
    pit_daily_closes,
    targets_to_orders,
)
from strategy_xs import (  # noqa: E402
    DEFAULTS,
    diversifier_basket,
    strategy_xs_universe,
    xs_targets,
)

try:
    from utils import log_message as _log
except Exception:  # pragma: no cover - broker-only import
    def _log(msg, color="white"):
        print(msg)

#: Every Strategy XS exit is a rebalance of an ETF book, which is what the
#: broker's sell whitelist calls `etf_sell`. Publishing it is not cosmetic:
#: `broker.py`'s Z2.1 check reads `action_intent` off the strategy summary, and
#: a sell with no recognised intent logs `would_block_in_phase2=True`. Measured
#: on Strategy X's BT406990, that was 965 of 965 sells — the whole book.
_SELL_INTENT = "etf_sell"


def _truthy(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"} \
        if not isinstance(value, bool) else value


def _bars_for(data, symbol):
    entry = (data or {}).get(symbol) or {}
    if isinstance(entry, dict):
        return entry.get("bars") or []
    return entry or []


class StrategyXS:
    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("strategy_xs_enabled", False)):
            return {}
        if portfolio_emulator is None:
            return {}

        prices = prices or {}
        cache = strategy_cache if isinstance(strategy_cache, dict) else {}
        filt = str(cfg.get("core_filter_symbol", "QQQ") or "QQQ").strip().upper()
        closes = pit_daily_closes(_bars_for(data, filt), current_time)

        ma_bars = max(2, int(cfg.get("core_filter_ma_bars", 200) or 200))
        if len(closes) < ma_bars:
            _log(f"StrategyXS: REFUSING to trade — {len(closes)} daily closes "
                 f"for {filt}, need {ma_bars}. The filter cannot arm, and "
                 "'risk-off' would be a real cash buy, not a flat.", "red")
            return {}

        sig = core_signal(closes, cfg)
        vol_scale = core_vol_scale(closes, cfg)

        # Prices the broker did not carry: the declared legs are absent from
        # the operator's watchlist, so fall back to the last VISIBLE close,
        # which is the same number a quote would carry on this bar.
        eff = {str(s).strip().upper(): v for s, v in prices.items()}
        for symbol in strategy_xs_universe(cfg):
            if float(eff.get(symbol) or 0.0) > 0:
                continue
            visible = pit_daily_closes(_bars_for(data, symbol), current_time)
            if visible and float(visible[-1]) > 0:
                eff[symbol] = float(visible[-1])

        member_closes = {
            s: pit_daily_closes(_bars_for(data, s), current_time)
            for s in (cfg.get("diversifier_symbols") or [])
        }
        basket = diversifier_basket(member_closes, eff, cfg)

        targets, notes = xs_targets(risk_on=bool(sig.risk_on), config=cfg,
                                    basket=basket, satellite_ranked=None,
                                    vol_scale=vol_scale)

        nav = float(portfolio_emulator.get_portfolio_value(eff) or 0.0)
        positions = portfolio_emulator.get_positions() or {}
        cash = float(portfolio_emulator.get_cash() or 0.0)
        owned = set(strategy_xs_universe(cfg))
        decisions, sizes = targets_to_orders(
            targets, nav=nav, positions=positions, prices=eff, cash=cash,
            config=cfg, owned=owned)

        cache["_strategy_xs_last"] = {
            "risk_on": bool(sig.risk_on), "reason": sig.reason,
            "targets": dict(targets), "basket": list(basket),
            "vol_scale": vol_scale, "notes": list(notes),
        }
        _log(f"StrategyXS {'RISK-ON' if sig.risk_on else 'RISK-OFF'} | "
             f"{sig.reason} | targets="
             + ", ".join(f"{s} {w:.1%}" for s, w in sorted(targets.items()))
             + f" | orders={len(decisions)} | nav=${nav:,.0f}", "cyan")
        for note in notes:
            _log(f"  {note}", "white")

        if not decisions:
            return {}
        out = dict(decisions)
        out["_nexus_position_sizes"] = sizes
        out["_nexus_discovered"] = strategy_xs_universe(cfg)
        out["_nexus_executable_buys"] = [s for s, d in decisions.items()
                                         if d == 1]
        out["_nexus_sell_enforcement"] = [s for s, d in decisions.items()
                                          if d == -1]
        out["_nexus_action_intents"] = {
            s: _SELL_INTENT for s, d in decisions.items() if d == -1}
        return out
```

- [ ] **Step 4: Sync the schema header**

Run: `python3 scripts/strategy_xs_sync_schema.py`
Expected: `schema synced from DEFAULTS: 26 config keys`

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/test_strategy_xs_run_once.py -q`
Expected: PASS, 8 tests

- [ ] **Step 6: Run the whole Strategy X suite to prove nothing regressed**

Run: `python3 -m pytest backend/tests/ -q -k "strategy_x or strategy_xs"`
Expected: PASS — Strategy X's existing tests must be untouched.

- [ ] **Step 7: Commit**

```bash
git add backend/strategies/strategy_xs.py \
        backend/tests/test_strategy_xs_run_once.py
git commit -m "feat(strategy-xs): broker-facing wrapper

Reuses core_signal and core_vol_scale from strategy_x unchanged rather than
forking two copies of the same boundary. Publishes _nexus_action_intents on
every sell, which Strategy X shipped without — all 965 of its sells logged
would_block_in_phase2=True, and when that check starts enforcing a strategy
with no recognised intent can never sell again."
```

---

### Task 5: Broker wiring for the declared universe

**Files:**
- Modify: `backend/broker.py` (`_strategy_x_universe_symbols` region, ~line 4300)
- Test: `backend/tests/test_strategy_xs_broker_wiring.py`

**Interfaces:**
- Consumes: `strategy_xs_universe` from Task 3.
- Produces: `_strategy_xs_universe_symbols(cached_strategies) -> list[str]`, and `_strategy_x_prepare` now prices XS's legs too.

- [ ] **Step 1: Run impact analysis first**

Run GitNexus upstream impact on `_strategy_x_prepare` and `_strategy_x_universe_symbols`. Report the risk level. Both are expected LOW (one call site each). **Stop and report if either is HIGH or CRITICAL.**

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_strategy_xs_broker_wiring.py`:

```python
"""Strategy XS must get bars and prices for legs the watchlist never lists."""
import ast
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

_BROKER = os.path.join(_BACKEND, "broker.py")


def _extract(*names):
    """AST-extract broker functions into a stub namespace.

    broker.py argparses at module scope and SystemExits under pytest, so it
    cannot be imported. This is the same technique the Strategy X broker tests
    use.
    """
    tree = ast.parse(open(_BROKER).read())
    wanted = [n for n in tree.body
              if isinstance(n, (ast.FunctionDef,)) and n.name in names]
    ns = {"mode": "backtest", "MODE_BACKTEST": "backtest",
          "MODE_LIVE": "live", "data_feed": None,
          "_log": lambda *a, **k: None}
    sys.path.insert(0, _BACKEND)
    exec(compile(ast.Module(body=wanted, type_ignores=[]), _BROKER, "exec"), ns)
    return ns


def spec(**config):
    return [{"strategy": "strategy_xs", "config": config}]


def test_the_declared_xs_universe_is_fetched():
    ns = _extract("_strategy_xs_universe_symbols")
    syms = ns["_strategy_xs_universe_symbols"](
        spec(strategy_xs_enabled=True, diversifier_pct=0.45))
    assert set(syms) >= {"QQQ", "TQQQ", "BIL", "GLD", "UUP", "DBMF"}


def test_a_disabled_xs_contributes_no_symbols():
    ns = _extract("_strategy_xs_universe_symbols")
    assert ns["_strategy_xs_universe_symbols"](
        spec(strategy_xs_enabled=False)) == []


def test_an_absent_xs_contributes_no_symbols():
    ns = _extract("_strategy_xs_universe_symbols")
    assert ns["_strategy_xs_universe_symbols"](
        [{"strategy": "graph_nexus_analysis", "config": {}}]) == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/test_strategy_xs_broker_wiring.py -q`
Expected: FAIL — `KeyError: '_strategy_xs_universe_symbols'`

- [ ] **Step 4: Write minimal implementation**

Add to `backend/broker.py`, immediately after `_strategy_x_universe_symbols`:

```python
def _strategy_xs_universe_symbols(cached_strategies):
    """Symbols strategy_xs needs bars for, from its own config.

    Same contract as `_strategy_x_universe_symbols` and the same reason: a
    strategy that trades symbols the operator never listed must declare them,
    or `price_history` is built without them and the strategy is silently
    inert. Returns [] when strategy_xs is absent or disabled.
    """
    try:
        from strategy_xs import DEFAULTS as _XS_DEFAULTS, strategy_xs_universe
    except Exception:
        return []
    out = []
    try:
        for spec in (cached_strategies or []):
            if not isinstance(spec, dict):
                continue
            name = str(spec.get("strategy", "")).strip().lower()
            if name not in {"strategy_xs", "strategyxs"}:
                continue
            merged = {**_XS_DEFAULTS, **(spec.get("config") or {})}
            if not merged.get("strategy_xs_enabled", False):
                continue
            for s in strategy_xs_universe(merged):
                if s and s not in out:
                    out.append(s)
    except Exception:
        return []
    return out
```

Then in `_strategy_x_prepare`, change the symbol source line so both
strategies' declared legs are priced. Find:

```python
        syms = [s for s in _strategy_x_universe_symbols(cached_strategies)
                if s and not (prices or {}).get(s)]
```

and replace with:

```python
        # BOTH declaring strategies. Strategy XS uses the identical contract,
        # and a second prepare pass in the hot path would double the fetches
        # for the symbols they share (QQQ, TQQQ, BIL).
        declared = list(_strategy_x_universe_symbols(cached_strategies))
        for s in _strategy_xs_universe_symbols(cached_strategies):
            if s not in declared:
                declared.append(s)
        syms = [s for s in declared if s and not (prices or {}).get(s)]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/test_strategy_xs_broker_wiring.py -q`
Expected: PASS, 3 tests

- [ ] **Step 6: Run the broker and Strategy X suites**

Run: `python3 -m pytest backend/tests/ -q -k "strategy_x or strategy_xs or broker"`
Expected: PASS — no Strategy X regression.

- [ ] **Step 7: Commit**

```bash
git add backend/broker.py backend/tests/test_strategy_xs_broker_wiring.py
git commit -m "feat(strategy-xs): fetch and price the strategy's declared legs

Same contract as strategy_x: a strategy trading symbols the operator never
listed must declare them, or price_history is built without them and the
strategy is silently inert. One prepare pass covers both, because they share
QQQ, TQQQ and BIL and a second pass would double those fetches."
```

---

### Task 6: The research harness and the frozen gate

**Files:**
- Create: `scripts/strategy_xs_matrix.py`

**Interfaces:**
- Consumes: `StrategyXS` from Task 4.
- Produces: a printed report and an optional JSON artifact at `$SX_MATRIX_OUT`.

- [ ] **Step 1: Write the harness**

Create `scripts/strategy_xs_matrix.py`, modelled on
`scripts/strategy_x_bear_regime_matrix.py` (read it first — same `Emu`, same
next-bar fill convention, same `BAR_WINDOW` trimming). It must differ in four
ways:

1. `UNIVERSE = ["QQQ", "TQQQ", "BIL", "GLD", "UUP", "DBMF", "SPY", "SQQQ", "PSQ"]`.
2. Drive `StrategyXS` rather than `StrategyX`.
3. `COST_BPS = 23.0`, with the calibration comment carried over verbatim —
   2 bps made the Strategy X harness overstate return twofold.
4. Report the **calendar-year table** as the primary output, not window slices.
   Fifteen window slices flatter a strategy because some are six weeks long.

The gate, printed at the end and evaluated exactly as pre-registered:

```python
def gate(strategy, spy):
    """The four frozen conditions. All must hold."""
    s_yr = strategy.resample("YE").last().pct_change().dropna()
    b_yr = spy.resample("YE").last().pct_change().dropna()
    checks = {
        "CAGR above SPY": cagr(strategy) > cagr(spy),
        "maxDD better than SPY": abs(maxdd(strategy)) < abs(maxdd(spy)),
        "no more losing years than SPY": (s_yr < 0).sum() <= (b_yr < 0).sum(),
        "halves agree in sign": all(
            cagr(h_s) > cagr(h_b) and abs(maxdd(h_s)) < abs(maxdd(h_b))
            for h_s, h_b in halves(strategy, spy)),
    }
    return checks
```

- [ ] **Step 2: Run it and confirm it reproduces the spec's headline**

Run: `python3 scripts/strategy_xs_matrix.py`
Expected: CAGR near 20.8%, maxDD near −24.6%, Sharpe near 1.04, 2 losing
years, 4 of 16 below SPY.

**If it does not reproduce within about 1pp, STOP and report.** The spec's
numbers came from a vectorised prototype; this is the real `run_once` and a
gap means the implementation differs from the design.

- [ ] **Step 3: Report the cost sensitivity**

Run the harness at `COST_BPS` of 2, 5, 10 and 23 and record all four. Reporting
only the favourable end is how the Strategy X harness overstated return by two
times.

- [ ] **Step 4: Commit**

```bash
git add scripts/strategy_xs_matrix.py
git commit -m "feat(strategy-xs): research harness with the frozen gate

Calendar years are the primary output, not window slices — fifteen slices
flatter a strategy because some are six weeks long. Costs are calibrated to the
engine's own fills at 23 bps, and the sensitivity is reported across 2/5/10/23
rather than only the favourable end."
```

---

### Task 7: Engine validation and the evidence report

**Files:**
- Create: `docs/superpowers/research/2026-08-27-strategy-xs-results.md`
- Create: `scripts/_sx_xs_run.py` (launcher, mirroring `scripts/_sx_bear_run.py`)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Deploy and verify**

```bash
git push origin main
# wait 5 minutes, then:
python3 scripts/check_deployed_code.py
```
Expected: `All 9 files match.` **Do not run a backtest until this is clean** —
this project has twice drawn conclusions from code that was never live.

- [ ] **Step 2: Create the instance and strategy document**

Create a NEW strategy document and a NEW instance for XS. **Do not reuse
document 198 or instance `strategy-x`** — Strategy X's evidence must stay
reproducible. Snapshot the new document's canonical SHA-256 before any run.

- [ ] **Step 3: Clear state twice, then launch**

The second clear must delete zero rows. A non-zero second clear means state was
still being written and the arm would not start cold; cold-start equality is
what takes the A/A noise floor from 10pp to 0.5pp.

Window `2021-11-01` to today, granularity `86400`, initial cash `6000.0`,
stocks `["QQQ"]` only — the strategy self-declares the rest, and passing the
full list would hide a regression in that path.

- [ ] **Step 4: Pull the logs and verify before trusting the number**

```bash
python3 scripts/pull_backtest_logs.py <ID> --out /tmp/bt_xs.log
```

Confirm from the log, not from the summary: all six declared symbols received
fills; targets sum to 1.0 every session; the diversifier is held in BOTH
regimes; risk-off holds BIL and not SPY; and every sell carries
`action_intent=etf_sell` with no `would_block_in_phase2=True`.

The engine has repeatedly exited at ~100% without persisting `final_value`, so
reconstruct the equity curve from the log's `nav=$X` lines against the
`[Pending] YYYY-MM-DD` markers if the terminal row is missing.

- [ ] **Step 5: Write the evidence report**

Create `docs/superpowers/research/2026-08-27-strategy-xs-results.md` with: the
four gate conditions and whether each passed, the calendar-year table against
SPY and against Strategy X, the cost sensitivity, turnover per year, realised
beta, and a section named "what this does not do" carrying the 2018/2022
negatives and the 7.3-year limit on any DBMF-dependent number.

**If the gate fails, the report says so and the strategy stays disabled.** Do
not re-tune to reach the gate — the gate was frozen before the run precisely so
that it cannot be moved afterwards.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/research/2026-08-27-strategy-xs-results.md \
        scripts/_sx_xs_run.py
git commit -m "docs: Strategy XS engine validation"
```

---

## Self-Review

**Spec coverage.** Allocation model → Task 2. Diversifier basket and its
eligibility rules → Task 1. Regime handling and the cash risk-off → Task 2 and
Task 4. Configuration surface → Task 2 `DEFAULTS`. Fail-safes → Tasks 1, 2 and
4. Validation protocol and frozen gate → Task 6. Universe declaration → Task 3
and Task 5. The inverse sleeve shipping default-off with its evidence → Task 2
`DEFAULTS`. Live blocker → recorded in the spec; no task, because it is a
`live_risk_state` change outside this strategy's scope and needs its own
approval.

**Type consistency.** `diversifier_basket` returns a `tuple` in Task 1 and is
consumed as `basket=` in Tasks 2 and 4. `xs_targets` returns
`tuple[dict, list]` in Task 2 and is unpacked as `targets, notes` in Task 4.
`strategy_xs_universe` returns `list[str]` in Task 3 and is consumed by Task 4
and Task 5. `DEFAULTS` key names in Task 2 match those read in Tasks 3 and 4.

**Known gap, deliberate.** Task 4 passes `satellite_ranked=None`. The Graph
sleeve is specced at `satellite_pct=0.0` and arming it needs a ranking source
plus its own evidence, so wiring the conviction-score channel is out of scope
here. `xs_targets` already accepts and tests the parameter, so arming it later
is a wrapper change only.
