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
    "satellite_max_share",
    "turnover_budget_state",
    "funding_release_reserve_state",
    "reset_funding_release_reserve",
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
    #: 2026-08-09 FUNDING-RELEASE RESERVE. 0 == OFF == today's behaviour, byte
    #: for byte. See `_FUNDING_RELEASE_RESERVE` and the note on the deploy
    #: branch of `core_rebalance_order` for what it buys and what it costs.
    funding_release_reserve_decisions: int = 0
    #: 2026-08-14 ALPHA HEADROOM. Fraction of NAV the deploy leaves unspent so
    #: the alpha book can still open one position on the next bar. 0.0 == OFF ==
    #: today's behaviour, byte for byte. See the deploy branch below.
    deploy_alpha_headroom_pct: float = 0.0


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
    design = max(0.05, min(0.95, 1.0 - core_tgt - cash_fl))
    # 2026-08-08 CONVICTION RESERVE (default 0.0 = off), bt 613166.
    #
    # The book commits its whole risk budget on day one to whatever happens to
    # be available then, and cannot make room for a better name that shows up
    # later. bt 613166 opened NVDA/HESM/NTR at 14% each, and when SNDK arrived
    # on 02-05 at raw=+1.705 the allocator sized it correctly at $872 and the
    # sleeve had nothing left to give it:
    #
    #   SATELLITE OVERFLOW: SNDK raw=+1.705 >= 1.50 —
    #                       funding $168 of room out of the core (floor-bounded)
    #   Buy gate: cash=$150.12 -> FILL $87.45 = 1.5% of NAV
    #
    # The conviction band is only (max_share - design_share) wide — 10 points on
    # the current config — so once plain buys have taken the design share there
    # is no room for the name the band exists to fund.
    #
    # Withholding a slice of the DESIGN share from plain buys widens that band
    # without touching the core's target or its floor: the core still aims at
    # `core_target_pct` and is still bounded by `core_min_pct`, and a conviction
    # name can still reach `satellite_max_share`. Plain buys simply stop short,
    # so the reserve is there when a big mover appears.
    #
    # Only affects the DESIGN share; `satellite_max_share` is deliberately
    # untouched, which is what makes this a reserve rather than a de-risk.
    reserve = _f(cfg, "satellite_conviction_reserve_pct", 0.0)
    if reserve > 0:
        design = max(0.05, design - reserve)
    return design


def satellite_max_share(config, *, regime=None) -> float:
    """Hard ceiling on the satellite — the share at which the core hits its FLOOR.

    2026-08-07, bt 677976. `satellite_design_share` is a *design* weight, and
    treating it as a hard ceiling pins the satellite at exactly that number
    forever: headroom is born at $0 and a new name can only enter by backfilling
    someone's exit. Measured on that run, SNDK entered on 2026-01-30 — the same
    bar another name was sold — for $156, and captured 2.7% of a +166% move
    because it had gone in at 96.7% of the way through it.

    That contradicts the sleeve's own design, which says a graph BUY should
    RAISE the satellite and lower the core: "the name is held ABOVE its index
    weight, funded by selling the index (an overweight)". `core_target_weight`
    already implements exactly that (core is the residual of the satellite). The
    static cap was added to stop the satellite crowding the core to its floor,
    and over-corrected into never letting it move at all.

    The real guardrail is `core_min_pct` (0.30, already enforced by
    `core_target_weight`'s clamp and covered by tests). This returns the
    satellite share at that floor — 0.68 on the defaults — so a
    high-conviction name can be funded out of the index while the core is still
    protected by the same floor it always had. Never below the design share.
    """
    cfg = config or {}
    cash_fl = _f(cfg, "cash_reserve_floor_pct", 0.02)
    core_min = _f(cfg, "core_min_pct", 0.30)
    ceiling = max(0.05, min(0.95, 1.0 - core_min - cash_fl))
    return max(ceiling, satellite_design_share(cfg, regime=regime))


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
        funding_release_reserve_decisions=max(0, _i(
            cfg, "core_funding_release_reserve_decisions", 0)),
        deploy_alpha_headroom_pct=max(0.0, _f(
            cfg, "core_deploy_alpha_headroom_pct", 0.0)),
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


# ── funding-release reserve (2026-08-09, default OFF) ─────────────────────
#
# THE DEFECT, measured. `sweep2.md` §2c/§2d, verbatim off bt 427197:
#
#   L4101  [core] funding request trimmed $1,709 -> $1,669 — satellite headroom
#          will refuse the remainder; releasing core for it would only be bought back
#   L4112  SATELLITE OVERFLOW: ARWR raw=+1.750 >= 1.50 — funding $1,669 of room
#   L4359  [execution] FILL SELL SPY qty=2.44 price=686.74  = $1,675.74
#   L4115  SKIP BUY ARWR — cash_to_use $1.69 < min $366 (allocated $854.39)
#   L4582  [core] bought $1546.03 SPY @ 687.73 (band_deploy: 12.1% -> 37.6% of NAV)
#
# The core sold $1,675.74 of SPY to fund two named conviction buys, and one bar
# later `band_deploy` bought $1,545.98 (92.3%) of it straight back, because
# `buy = min(drift_usd, _spendable)` has NO knowledge that the gap it is closing
# was opened ON PURPOSE, one bar ago, to fund a buy that is still queued. The
# drift it "corrects" IS the release.
#
# Not a window artefact — same shape in 4 runs, 3 windows, 2 regimes:
#   427197 bull   released $3,703 -> re-bought $2,505 (68%)
#   915207 bull   released $1,410 -> re-bought $1,317 (93%)
#   542754 bear   released $3,924 -> re-bought $1,567 (40%)
#   383778 OOS    released $2,437 -> re-bought $1,715 (70%)
#   total         $11,474 released, $7,104 recycled (62%)
#
# THE RULE. A `funding` release is a RESERVED CREDIT: dollars already promised
# to a named satellite buy. While that credit is outstanding, `band_deploy` may
# not count it as spendable cash. It is not a permanent block — the credit
# expires after `core_funding_release_reserve_decisions` core deploy decisions
# that it actually refuses, so cash the satellite declines to spend still comes
# home to the index instead of sitting dead.
#
# WHY MODULE STATE. The credit has to survive from the bar the release is
# decided to the bar the satellite spends it, and `core_rebalance_order` is a
# pure function called fresh each bar from broker.py's `_core_sleeve_decide`.
# Adding a parameter would need a broker.py change; this module owns the rule,
# so it owns the ledger. One core sleeve exists per process (broker's
# `_core_sleeve_cfg` returns the first enabled spec), so one ledger is correct.
# It is deliberately NOT persisted: a restart drops the credit, which fails
# toward today's behaviour rather than toward a core that can never re-deploy.
#
# WHY "DECISIONS" AND NOT "BARS". broker.py evaluates the core TWICE per bar —
# `_residual_sleeve_release` at cycle start (which DISCARDS a positive notional,
# broker.py:4481) and `_residual_sleeve_deploy` at cycle end (which executes
# it). Both reach the deploy branch below, both can be refused, and this module
# is handed no clock that could tell them apart: `nav`/`cash` move within a bar
# and `days_since_rebalance` is day-granular and resets. So the unit is one
# REFUSED deploy decision.
#
# Counted against the real wiring, protecting the satellite for ONE bar costs
# THREE decisions, because the funding sell fills before the release bar's own
# cycle-end deploy:
#     bar N   release  -> `funding`, arms the credit          (no decision)
#     bar N   deploy   -> refused #1
#     bar N+1 release  -> refused #2   (positive notional, discarded anyway)
#     bar N+1 deploy   -> refused #3   <- the re-buy the finding is about
#     bar N+2 release  -> refused #4, or expiry
# `test_core_funding_release_reserve.py` pins that ladder against broker.py's
# own extracted functions, so it cannot drift silently. 4 is the value to ship:
# it covers the bar with one decision of margin. Bias UP, not down — a credit
# that expires early is an INERT lever (five of those shipped this session),
# while a credit that expires late only leaves the index a bar of cash behind.
#
# A decision is consumed ONLY when the reserve changes the outcome: the
# unreserved order would have been a real deploy and the reserved one is not.
# A bar the core was never going to deploy on costs nothing.
_FUNDING_RELEASE_RESERVE: dict = {"usd": 0.0, "decisions": 0}


def funding_release_reserve_state() -> dict:
    """Read-only snapshot of the outstanding funding credit. For tests and for
    an operator who wants to know why the core is holding cash."""
    return dict(_FUNDING_RELEASE_RESERVE)


def reset_funding_release_reserve() -> None:
    """Drop any outstanding credit. Called by tests; a fresh process starts
    here anyway."""
    _FUNDING_RELEASE_RESERVE["usd"] = 0.0
    _FUNDING_RELEASE_RESERVE["decisions"] = 0


def _arm_funding_release_reserve(cfg: CoreSleeveConfig, released_usd: float,
                                 nav: float) -> None:
    """Record a `funding` release as a reserved credit. No-op when OFF."""
    budget = int(getattr(cfg, "funding_release_reserve_decisions", 0) or 0)
    if budget <= 0:
        return
    usd = float(_FUNDING_RELEASE_RESERVE.get("usd", 0.0) or 0.0)
    # Accumulate: two releases on consecutive bars for two different names are
    # two credits. Capped at NAV so a pathological loop cannot grow it without
    # bound; the effective claim is capped again by actual cash at bite time.
    usd = min(max(0.0, usd) + max(0.0, float(released_usd or 0.0)),
              max(0.0, float(nav or 0.0)))
    _FUNDING_RELEASE_RESERVE["usd"] = usd
    # A NEW release refreshes the budget: the satellite gets its bar for THIS
    # credit, not the leftovers of the previous one's clock.
    _FUNDING_RELEASE_RESERVE["decisions"] = budget


def _outstanding_funding_reserve(cfg: CoreSleeveConfig, spendable: float) -> float:
    """Dollars of `spendable` that a funding release has already promised away.

    Capped at `spendable`, which is what makes the credit self-clearing in the
    good case: once the satellite has actually bought the name, the cash is gone
    and the claim collapses to $0 without any bookkeeping.
    """
    if int(getattr(cfg, "funding_release_reserve_decisions", 0) or 0) <= 0:
        return 0.0
    if int(_FUNDING_RELEASE_RESERVE.get("decisions", 0) or 0) <= 0:
        return 0.0
    usd = float(_FUNDING_RELEASE_RESERVE.get("usd", 0.0) or 0.0)
    if usd <= 0.0:
        return 0.0
    return min(usd, max(0.0, float(spendable or 0.0)))


def _consume_funding_reserve_decision() -> None:
    """Spend one unit of the credit's expiry budget; clear it at zero."""
    left = int(_FUNDING_RELEASE_RESERVE.get("decisions", 0) or 0) - 1
    _FUNDING_RELEASE_RESERVE["decisions"] = max(0, left)
    if left <= 0:
        _FUNDING_RELEASE_RESERVE["usd"] = 0.0


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
            # These dollars are already promised to a named satellite buy.
            # Reserve them so `band_deploy` below cannot re-buy them as index
            # before the allocator has had its bar. OFF by default.
            _arm_funding_release_reserve(cfg, sell, nav)
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
        # 2026-08-14 ALPHA HEADROOM (default 0.0 = OFF = byte-identical).
        #
        # THE DEFECT, bt 523085 (W0), reconciled to the cent. This clip is
        # CASH-bound, not drift-bound, on four of the run's five deploys: the
        # core sat at 11.6-12.1% of NAV against a 27-30% target for the whole
        # window, so `drift_usd` always exceeded `_spendable` and `buy`
        # collapsed to "every dollar there is". At L13194 that was
        # (1299.21 - 0.02*6329)/1.006 = $1,165.64 against a logged $1,165.72.
        #
        # Under next-event execution the deploy's order pends into the NEXT
        # bar, and its `_execution_cash_reservations` entry is invisible to the
        # alpha buy gate (which reads `get_cash()`) but binding on the executor
        # (which reads `get_buying_power(reserved)`). So the alpha book sees
        # PASS and is then refused:
        #     L13631  Buy gate inputs for AMZN: cash=$1299.21 reserved=$0.00 -> PASS
        #     L13632  SKIP BUY AMZN - fundable $133.49 ... < min $379
        # $1,299.21 - $1,165.72 = $133.49. All five core deploys in that run
        # held at least one gate-passed alpha buy hostage; $5,774.16 of core
        # notional against $5,756.28 deployed.
        #
        # WHY A CLIP AND NOT A VETO. Because the deploy is cash-bound, a gate
        # of the form "hold while alpha has queued buys" fires on essentially
        # every bar (the run logged a funding request on 41 of 41) and the core
        # deploys ZERO times. Leaving headroom keeps the core deploying — on
        # the measured run four of the five deploys survive at 0.07 — while
        # leaving enough behind for the alpha book to clear its min-position
        # floor, which is `max($50, NAV*min_position_nav_pct)`, i.e. 6% of NAV
        # on the documents that set it. 0.07 clears that with a margin.
        #
        # The cost is real and unconditional: the headroom sits idle whenever
        # the core is cash-bound, whether or not the alpha book uses it. The
        # conditional version (withhold only against a live, actionable funding
        # request) needs `funding_request` threaded into the deploy call site,
        # which today passes none — broker.py:5143 calls `_core_sleeve_decide`
        # without it, so the whole funding branch above is dead code on the
        # deploy path. That is a second behaviour and belongs in its own A/B.
        _alpha_headroom = max(0.0, float(
            getattr(cfg, "deploy_alpha_headroom_pct", 0.0) or 0.0)) * nav
        if _alpha_headroom > 0.0:
            # Never surrender more than half of what the core can see. A
            # mis-set flag on a fully-invested book would otherwise pin the
            # core below its band permanently with no way back in.
            _alpha_headroom = min(_alpha_headroom, 0.5 * _spendable)
        buy = min(drift_usd, max(0.0, _spendable - _alpha_headroom))
        _min_deploy = max(MIN_CORE_DEPLOY_USD, MIN_CORE_ORDER_USD)
        if buy < _min_deploy:
            # Refused for size, with or without a reserve. Return BEFORE the
            # reserve is consulted so a bar the core was never going to deploy
            # on cannot burn a unit of the credit's expiry budget.
            #
            # Say WHICH refusal it was. If the un-reserved clip would have
            # cleared the minimum, the headroom is what refused this deploy and
            # `_core_sleeve_log_hold` must show that rather than hiding it
            # inside `deploy_below_min` — five levers have shipped inert in
            # this project and each was invisible for exactly that reason.
            if _alpha_headroom > 0.0 and min(drift_usd, _spendable) >= _min_deploy:
                return RebalanceOrder(reason="deploy_alpha_headroom", **base)
            return RebalanceOrder(reason="deploy_below_min", **base)
        # 2026-08-09 FUNDING-RELEASE RESERVE (default OFF, see the note above
        # RebalanceOrder). `buy` is currently sized against cash the core itself
        # may have released one bar ago to fund a conviction name that has not
        # been bought yet — measured at 62% recycling across 4 runs, $7,104.
        # Withhold that credit, and say so in the reason so the hold is visible
        # in the operator log instead of being silently indistinguishable from
        # `within_band`.
        _reserved = _outstanding_funding_reserve(cfg, _spendable)
        if _reserved > 0.0:
            _buy_reserved = min(drift_usd, max(0.0, _spendable - _reserved))
            if _buy_reserved < buy - 1e-9:
                # The reserve genuinely changed the outcome — this is the
                # re-buy the finding is about. Only now does it cost a unit.
                _consume_funding_reserve_decision()
                buy = _buy_reserved
                if buy < _min_deploy:
                    return RebalanceOrder(
                        reason="funding_release_reserved", **base)
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
