"""Index-core allocation sizing.

Design: docs/strategies/index-core-allocation-design.md

WIRED (2026-08-03). This module used to say "PROTOTYPE, no call sites" and that
is no longer true — broker.py imports `core_sleeve_config` from `_core_sleeve_cfg`,
`core_rebalance_order` from `_core_sleeve_decide`, and `turnover_budget_state`
from `_core_turnover_state`. The stale header cost a later session real time
(it reads as "this cannot be the cause" while `core_rebalance_order` was in fact
emitting the 23 retry-storm deploys of bt 383711), so keep it accurate.

Still DEFAULT-OFF — see the `core_sleeve_enabled` note below. Off and unwired are
different claims, and only the first one is true.

NOTE ON SCOPE: `core_sleeve_enabled` may be set in a `regime_profiles` overlay
rather than the base config, because `_apply_regime_profile` merges the overlay
into the shared spec BEFORE the sleeve reads it. That is the supported way to
run the core in a bull and leave a bear on the base (and its SQQQ hedge) —
arming the core globally routes bear de-risk to CASH and drops that hedge.

Why a separate module rather than more functions in broker.py: broker.py is not
import-safe (argparse runs at module scope and SystemExits under pytest), which
is why backend/tests/test_residual_sleeve.py has to ``ast``-extract the sleeve
functions and exec them into a stub namespace. Pure arithmetic does not need
that ceremony, and keeping it out of the 14k-line broker means the rules can be
read in one screen.

Everything here is DEFAULT-OFF. ``core_sleeve_enabled`` is False unless a config
sets it, and every entry point returns a no-op decision when it is False. Wiring
these functions into `_residual_sleeve_deploy` / `_residual_sleeve_release`
behind that flag therefore leaves today's behaviour byte-identical.

Vocabulary
----------
core       the broad-index position (SPY). Reuses ``residual_sleeve_symbol`` so
           it inherits the six existing `_sleeve_symbols` exemptions — the
           strategy's monitor, kill loop, fast-loser-cut and bear-book-trim
           already refuse to touch that symbol.
satellite  everything the graph picked: single names plus trend ETFs. Any held
           position that is not the core and not the (dead) bear leg.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CoreSleeveConfig",
    "RebalanceOrder",
    "core_sleeve_config",
    "core_sleeve_armed_for_bar",
    "core_target_weight",
    "core_rebalance_order",
    "satellite_design_share",
    "turnover_budget_state",
]

#: Alpaca rejects equity orders under $1 of notional, and the residual sleeve
#: already refuses anything under $5 on the release side
#: (broker.py `_RESIDUAL_SLEEVE_MIN_RELEASE_USD`). A core rebalance decided on
#: this bar fills on the next one, so the same $5 margin applies in both
#: directions: an order that slips under the broker floor between decision and
#: submission is a trade the account never made, and modelling it as filled is
#: how a backtest stops describing the account.
MIN_CORE_ORDER_USD = 5.0

#: Deploy has floored at max($50, min_deploy_pct * NAV) since it was written.
#: Keep the absolute leg so a rounding-error rebalance on a small book does not
#: emit dust the fee model charges 30.3 bps on for no exposure change.
MIN_CORE_DEPLOY_USD = 50.0

#: 2026-08-03 — size the deploy against the ALL-IN cost, not against the mid.
#:
#: A buy costs `shares * fill_price + fees`, and `fill_price` carries the half
#: spread (22.8 bps under `equity-measured-v3-nbbo23`). Sizing `buy` at exactly
#: the spendable balance therefore asks for a clip the account cannot afford by
#: precisely the execution cost, and BOTH the emulator
#: (`portfolio_emulator._execute_buy`: `if total > self.get_buying_power():
#: return False`) and Alpaca reject it. The emulator's own comment calls this
#: out: "a caller that sized against the mid now discovers it cannot afford the
#: whole clip -- which is precisely what happens live."
#:
#: Observed on bt 383711: every deploy while the core was under target asked for
#: the full spendable balance, failed, and — because the cadence clock only
#: stamped on CONFIRMED fills — re-issued on the very next tick. 23 identical
#: "79.5% -> 90.3%" decisions, 21 of them ok=False. The core could never reach
#: its target and the two that did fill only churned.
#:
#: 60 bps is ~2.6x the measured one-way cost. The headroom is deliberate: the
#: model is an average over a 61-fill sample whose p90 spread is 109 bps, and
#: underestimating here costs a whole rebalance window while overestimating
#: leaves a few dollars unspent that the next window picks up.
CORE_DEPLOY_COST_HAIRCUT = 0.0060


@dataclass(frozen=True)
class CoreSleeveConfig:
    """Parsed core-sleeve levers. Mirrors `_residual_sleeve_config`'s contract:
    one parse point, coerce everything, never raise out of the parser (a raise
    there silently disables the whole sleeve at three call sites)."""

    enabled: bool = False
    target_pct: float = 0.60
    min_pct: float = 0.30
    max_pct: float = 0.98
    cash_floor_pct: float = 0.02
    rebalance_band_pct: float = 0.05
    rebalance_min_days: int = 5
    bear_scale: float = 0.5
    bear_min_dwell_days: int = 3
    turnover_budget_monthly_pct: float = 0.0
    #: Largest fraction of NAV the bear de-risk may sell in ONE bar. 0 disables
    #: the cap, restoring the single-bar cliff. 0.15 completes a 68pp de-risk in
    #: five bars, which is well inside a real downtrend and survivable through a
    #: false positive.
    bear_max_step_pct: float = 0.15


def _f(cfg, key, default):
    try:
        value = cfg.get(key, default)
        if value is None or value == "":
            return float(default)
        out = float(value)
        # NaN/inf in a weight target would propagate into an order size.
        return out if out == out and abs(out) != float("inf") else float(default)
    except (TypeError, ValueError, AttributeError):
        return float(default)


def _i(cfg, key, default):
    try:
        value = cfg.get(key, default)
        if value is None or value == "":
            return int(default)
        return int(value)
    except (TypeError, ValueError, AttributeError):
        return int(default)


def _enabled_regime_profiles(config) -> list:
    """Regime overlays that actually switch the core ON, as (name, overlay)."""
    profiles = (config or {}).get("regime_profiles") or {}
    if not isinstance(profiles, dict):
        return []
    return sorted(
        (
            (str(name), over)
            for name, over in profiles.items()
            if isinstance(over, dict) and bool(over.get("core_sleeve_enabled", False))
        ),
        key=lambda pair: pair[0],
    )


def core_sleeve_armed_for_bar(config, *, regime=None) -> bool:
    """True when the core's design share should bound the satellite THIS bar.

    2026-08-07, bt 804832. `_apply_regime_profile` merges the matching overlay
    into the config before it is read, and returns the config UNCHANGED when no
    overlay matches. So an absent `core_sleeve_enabled` is ambiguous — it means
    EITHER "the detector has not picked a regime yet" (warm-up) OR "this regime
    has no profile, so the core is deliberately off" (doc-193 has no bear
    profile, and `test_regime_conditional_core` measures that bear arm at
    +10.07% precisely BECAUSE the core stays off and the hedge runs).

    `regime` disambiguates them, and it is the caller's detected regime for this
    bar (`strategy_cache["_market_regime"]`), or None during warm-up:

      * flag already True  -> armed (overlay merged and switched it on)
      * regime KNOWN       -> not armed (no overlay for it = core off by design)
      * regime UNKNOWN     -> armed if ANY profile enables the core

    Only the third branch is new behaviour, and it is the bug: on tick 1 the
    allocator's satellite clamp read the un-merged base config, saw False, and
    sized the opening basket against `nexus_portfolio_pct` (0.95) instead of the
    0.38 design share. It opened at 48.5% of NAV, headroom went negative on tick
    2 and never recovered — 43 `SATELLITE CAP` refusals across 33 names.

    An earlier version of this function scanned profiles unconditionally. That
    also armed the clamp on every BEAR bar, reserving ~62% of NAV for a core
    that would never be bought. Passing the regime is what keeps the fix scoped
    to warm-up.
    """
    cfg = config or {}
    if bool(cfg.get("core_sleeve_enabled", False)):
        return True
    named = str(regime or "").strip().lower()
    if named:
        # A regime IS known. Consult ITS profile directly rather than trusting
        # the flag on `config` — verified in bt 632754 that the overlay does NOT
        # reach the allocator's config even when the detector has long since
        # picked a regime (`V31 market regime: chop (closes=90)` fired two
        # minutes before the first buy, and the clamp still read False).
        #
        # Returning False here on the strength of the un-merged flag was the
        # first fix's own bug: it reproduced the original defect exactly.
        profiles = cfg.get("regime_profiles") or {}
        if isinstance(profiles, dict):
            for name, over in profiles.items():
                if str(name).strip().lower() == named and isinstance(over, dict):
                    return bool(over.get("core_sleeve_enabled", False))
        # No profile for this regime -> the core is off here BY DESIGN. doc-193
        # has no `bear` profile, and `test_regime_conditional_core` measures that
        # arm at +10.07% precisely because the core stays off and the hedge runs.
        return False
    # Regime not yet known: arm if the core is configured for any regime.
    return bool(_enabled_regime_profiles(cfg))


def satellite_design_share(config, *, regime=None) -> float:
    """Fraction of NAV the satellite sleeve is allowed to occupy.

    The SINGLE source of truth. Three sites computed this expression
    independently (broker `_core_sleeve_satellite_headroom` and
    `_core_sleeve_block_new_satellite`, and the allocator's total-spend cap in
    graph_nexus_analysis), which is how they came to disagree about whether the
    core was armed at all.

    ``core_target_pct`` is resolved regime-aware for the same reason as the
    enabled flag: it too lives only in the regime profiles, so reading it off
    the base config during warm-up silently falls back to the default.
    """
    cfg = config or {}
    target = cfg.get("core_target_pct")
    if target is None:
        # Only profiles that actually switch the core ON may define its target —
        # a profile with the core OFF has no say in how much room it reserves.
        # When `regime` names one of them, use it; otherwise take the LARGEST
        # core target among enabled profiles, which is the tightest satellite
        # share and therefore the conservative reading.
        #
        # The "first profile wins" version of this was order-dependent: RethinkDB
        # returns sorted keys, so `bear` would have won simply by being
        # alphabetically first — the one regime whose profile is most likely to
        # carry a de-risked target it never intended to impose on a bull bar.
        enabled = _enabled_regime_profiles(cfg)
        named = str(regime or "").strip().lower()
        chosen = [over for name, over in enabled if name.strip().lower() == named]
        pool = chosen or [over for _name, over in enabled]
        targets = [
            over.get("core_target_pct")
            for over in pool
            if over.get("core_target_pct") is not None
        ]
        if targets:
            target = max(_f({"v": t}, "v", 0.60) for t in targets)
    core_tgt = _f({"v": target}, "v", 0.60)
    cash_fl = _f(cfg, "cash_reserve_floor_pct", 0.02)
    return max(0.05, min(0.95, 1.0 - core_tgt - cash_fl))


def core_sleeve_config(config) -> CoreSleeveConfig:
    """Parse the core levers off a graph_nexus_analysis strategy config.

    Absent keys give the documented defaults, and ``enabled`` is False unless
    explicitly set, so an untouched doc-179 parses to a disabled core.
    """
    cfg = config or {}
    return CoreSleeveConfig(
        enabled=bool(cfg.get("core_sleeve_enabled", False)),
        target_pct=_f(cfg, "core_target_pct", 0.60),
        min_pct=_f(cfg, "core_min_pct", 0.30),
        max_pct=_f(cfg, "core_max_pct", 0.98),
        # Reuses the EXISTING key; the core does not invent a second cash floor.
        cash_floor_pct=_f(cfg, "cash_reserve_floor_pct", 0.02),
        rebalance_band_pct=_f(cfg, "core_rebalance_band_pct", 0.05),
        rebalance_min_days=_i(cfg, "core_rebalance_min_days", 5),
        bear_scale=_f(cfg, "core_bear_scale", 0.5),
        bear_min_dwell_days=_i(cfg, "core_bear_min_dwell_days", 3),
        turnover_budget_monthly_pct=_f(cfg, "turnover_budget_monthly_pct", 0.0),
        bear_max_step_pct=_f(cfg, "core_bear_max_step_pct", 0.15),
    )


def core_target_weight(cfg: CoreSleeveConfig, *, satellite_weight: float,
                       regime: str = "", bear_dwell_days: int = 0) -> float:
    """Target core weight as a fraction of NAV.

        core = clamp(1 - cash_floor - satellite, min_pct, max_pct)

    This single expression is the whole tilt rule, and the three behaviours the
    design asks for fall out of it rather than being special-cased:

      * a graph BUY raises ``satellite_weight``, lowering the core -> the name is
        held ABOVE its index weight, funded by selling the index (an overweight);
      * a graph SELL lowers it, raising the core -> proceeds go back into the
        index, never into cash (an underweight, i.e. index weight);
      * no opinion means ``satellite_weight == 0`` and the core goes to
        ``max_pct`` -> the default state is fully invested in the market, which
        is the entire point of the restructure.

    On a CONFIRMED bear that has persisted ``bear_min_dwell_days``, the target is
    scaled by ``bear_scale`` and the freed weight goes to CASH. The persistence
    gate is not decoration: `residual_sleeve_bear_min_dwell_days` exists in
    broker.py because 17 of 19 backtests parked a hedge on day 1 of a +12.8%
    month, and no price threshold separated that bar from a real bear.

    Returns 0.0 when disabled, so a caller that ignores ``enabled`` still cannot
    accidentally allocate.
    """
    if not cfg.enabled:
        return 0.0
    satellite = max(0.0, float(satellite_weight or 0.0))
    target = 1.0 - cfg.cash_floor_pct - satellite
    lo, hi = min(cfg.min_pct, cfg.max_pct), max(cfg.min_pct, cfg.max_pct)
    target = max(lo, min(hi, target))
    if str(regime or "").strip().lower() in ("bear", "crash"):
        if int(bear_dwell_days or 0) >= cfg.bear_min_dwell_days:
            # Scale the BASE, not the satellite-adjusted target: the de-risk is a
            # statement about how much index exposure to carry in a downtrend,
            # and it must not get larger just because the satellite happens to be
            # empty that bar. Still clamped to min_pct — halving is bounded where
            # today's `_residual_sleeve_release` zeroes the leg outright, which is
            # 60pp of one-way notional in a single bar.
            target = min(target, max(lo, cfg.target_pct * cfg.bear_scale))
    return target


@dataclass(frozen=True)
class RebalanceOrder:
    """A core rebalance decision. ``notional`` is signed: positive = BUY core,
    negative = SELL core, 0.0 = do nothing. ``reason`` is for the trade ledger
    and the operator log — every no-op says WHY, because the sleeve's failure
    mode has always been silence (`[sleeve] ENABLED but no SPY price` went
    unnoticed for a whole forensics cycle)."""

    notional: float = 0.0
    reason: str = "disabled"
    target_weight: float = 0.0
    current_weight: float = 0.0

    @property
    def is_action(self) -> bool:
        return abs(self.notional) > 0.0


def core_rebalance_order(
    cfg: CoreSleeveConfig,
    *,
    nav: float,
    core_value: float,
    satellite_value: float,
    cash: float,
    regime: str = "",
    bear_dwell_days: int = 0,
    days_since_rebalance: int | None = None,
    funding_request: float = 0.0,
    circuit_tier: str = "",
    turnover_exhausted: bool = False,
) -> RebalanceOrder:
    """Decide this bar's core order.

    The design's central change is here: today's sleeve triggers on a CASH LEVEL
    with a 2pp hysteresis band (park above 17% of NAV, release down to 15%), so
    every stock-lane trade drags cash across the band and the sleeve churns. On
    the run the brief cites (bt 987397) that produced $4,131 of SPY notional on a
    $6,000 book — 20.8% of NAV traded in 20 sessions to hold an average 4.2%
    position. This triggers on a WEIGHT BAND plus a cadence floor instead.

    Three things bypass the cadence, all of them either already-approved or
    risk-reducing:

    ``funding_request``  cash the allocator needs for a satellite buy it has
                         already approved. Instant, or the allocator starves —
                         this is the sleeve's original job.
    bear de-risk         a target cut by ``bear_scale`` releases immediately.
    turnover budget      an exhausted budget still permits SELLS toward target;
                         a budget that traps you in a position is worse than the
                         cost it saves.

    ``circuit_tier`` in ("hard", "kill") blocks BUYS only, mirroring the existing
    `_sleeve_circuit_tier` guard in `_residual_sleeve_deploy` — a core must not
    be added to while the drawdown circuit is tripped, but it must still be able
    to shrink.
    """
    if not cfg.enabled:
        return RebalanceOrder(reason="disabled")
    nav = float(nav or 0.0)
    if nav <= 0.0:
        return RebalanceOrder(reason="no_nav")

    core_value = max(0.0, float(core_value or 0.0))
    current_w = core_value / nav
    satellite_w = max(0.0, float(satellite_value or 0.0)) / nav
    target_w = core_target_weight(
        cfg, satellite_weight=satellite_w, regime=regime,
        bear_dwell_days=bear_dwell_days,
    )
    base = {"target_weight": target_w, "current_weight": current_w}

    # 1) Funding: release exactly what an approved satellite buy needs, no more.
    #    Sized off the shortfall, not off a cash target — sizing off a target is
    #    what makes the current sleeve release more than the book asked for and
    #    then re-park it on the next bar.
    need = max(0.0, float(funding_request or 0.0) - max(0.0, float(cash or 0.0)))
    if need > 0.0 and core_value > 0.0:
        sell = min(need, core_value)
        if sell >= MIN_CORE_ORDER_USD:
            return RebalanceOrder(notional=-sell, reason="funding", **base)
        # FALL THROUGH, do not return.
        #
        # 2026-08-03 adversarial sweep, MED-HIGH: this used to return
        # `funding_below_min` here, which meant a shortfall of $0.01-$4.99
        # silently cancelled EVERYTHING below -- including the bear de-risk. A
        # book at 98% core in a confirmed bear with dwell 9 did no de-risking at
        # all because the allocator happened to be $1 short that bar, and the
        # condition can persist for many bars. Deploy cannot compensate: it owns
        # the buy side only.
        #
        # A funding request too small to act on is not a reason to skip risk
        # reduction. It is a reason to skip FUNDING.

    drift = target_w - current_w
    drift_usd = drift * nav

    # 2) Bear de-risk: bypasses the cadence, but only in the SELL direction. A
    #    bear that wants MORE core is just a normal rebalance and waits its turn.
    bear_now = (
        str(regime or "").strip().lower() in ("bear", "crash")
        and int(bear_dwell_days or 0) >= cfg.bear_min_dwell_days
    )
    if bear_now and drift_usd < 0.0:
        # Step-capped, because the de-risk this design REPLACES was criticised
        # for dumping 60pp of one-way notional in a single bar -- and unstepped,
        # this one is worse.
        #
        # 2026-08-03 adversarial sweep, HIGH: the DEFAULT state of this design is
        # a ~98% core (a silent graph means satellite 0, so the residual clamps
        # to max_pct). bear_scale=0.5 then drives the target to
        # target_pct*0.5 = 30%, i.e. a 68%-of-NAV sell in ONE bar, bypassing the
        # cadence, on a detector whose own comment says it lags a V-bottom by
        # about two sessions. The existing test only proved frac < 1.0 on a 60%
        # core; it never exercised the state the design actually produces.
        #
        # Capping the STEP keeps the de-risk (it completes over a few bars) while
        # removing the single-bar cliff. A real downtrend persists long enough
        # for several steps; a two-session false positive costs one step, not the
        # whole book.
        step_cap = max(0.0, float(cfg.bear_max_step_pct or 0.0)) * nav
        sell = min(-drift_usd, core_value)
        if step_cap > 0.0:
            sell = min(sell, step_cap)
        if sell < MIN_CORE_ORDER_USD:
            return RebalanceOrder(reason="bear_derisk_below_min", **base)
        return RebalanceOrder(notional=-sell, reason="bear_derisk", **base)

    # 3) Band. Below it, hold — this is what collapses the sleeve's turnover.
    if abs(drift) <= cfg.rebalance_band_pct:
        return RebalanceOrder(reason="within_band", **base)

    # 4) Cadence. Only after the band has already been breached, so a book that
    #    is inside the band never even consults the clock.
    if days_since_rebalance is not None and \
            int(days_since_rebalance) < cfg.rebalance_min_days:
        return RebalanceOrder(reason="cadence_hold", **base)

    if drift_usd > 0.0:
        if turnover_exhausted:
            return RebalanceOrder(reason="turnover_budget_exhausted", **base)
        if str(circuit_tier or "").strip().lower() in ("hard", "kill"):
            return RebalanceOrder(reason="circuit_blocks_add", **base)
        # Reserve the declared cash floor.
        #
        # 2026-08-03 sweep, MED: `core = clamp(1 - cash_floor - satellite, ...)`
        # only reserves the floor while the raw expression sits INSIDE the
        # clamp. Once the satellite pushes it below min_pct the clamp lifts the
        # target back up, and an unguarded `min(drift, cash)` then spent every
        # dollar -- a 90% satellite with $1,000 cash bought $1,000 of core and
        # left $0 against a declared 2% floor. That is the same state that
        # produced the "core pinned at 30.0%" symptom, now with no cash buffer
        # to trade out of it.
        _spendable = max(0.0, float(cash or 0.0) - cfg.cash_floor_pct * nav)
        # Reserve the execution cost of the clip itself. Without this the buy is
        # sized at the full spendable balance whenever the drift exceeds it —
        # the exact state the core is in while it is UNDER target — and is
        # rejected for being unaffordable by the spread it has to pay.
        # See CORE_DEPLOY_COST_HAIRCUT.
        _spendable /= (1.0 + CORE_DEPLOY_COST_HAIRCUT)
        buy = min(drift_usd, _spendable)
        if buy < max(MIN_CORE_DEPLOY_USD, MIN_CORE_ORDER_USD):
            return RebalanceOrder(reason="deploy_below_min", **base)
        return RebalanceOrder(notional=buy, reason="band_deploy", **base)

    sell = min(-drift_usd, core_value)
    if sell < MIN_CORE_ORDER_USD:
        return RebalanceOrder(reason="release_below_min", **base)
    return RebalanceOrder(notional=-sell, reason="band_release", **base)


def turnover_budget_state(cfg: CoreSleeveConfig, *, rolling_notional: float,
                          nav: float) -> tuple[bool, float]:
    """``(exhausted, used_pct)`` for the rolling 21-session one-way notional.

    ``used_pct`` is a fraction of NAV (0.50 == 50%/month, the Novy-Marx &
    Velikov line above which net anomaly spreads stop being significant). The
    system currently runs at ~2.90 live and ~4.66 in the bull-window backtest.

    0.0 == OFF, so an unconfigured book is never budget-blocked.
    """
    nav = float(nav or 0.0)
    if not cfg.enabled or cfg.turnover_budget_monthly_pct <= 0.0 or nav <= 0.0:
        return False, 0.0
    used = max(0.0, float(rolling_notional or 0.0)) / nav
    return used >= cfg.turnover_budget_monthly_pct, used
