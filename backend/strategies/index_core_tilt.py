# INTELLISTOCK_SCHEMA: {"strategy": "IndexCoreTilt", "weight": 1.0, "execution_position": 0, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"core_symbol": "SPY", "core_target_pct": 0.85, "core_band_pct": 0.05, "satellite_max_names": 6, "satellite_min_score": 0.0, "rebalance_min_days": 90, "min_hold_days": 30, "rank_band_exit_rank": 12, "enabled": false}}
# INTELLISTOCK_DESCRIPTION: Index core (SPY) plus a small, strictly-bounded conviction tilt. Built 2026-08-03 to TEST whether the graph signal is worth anything, at a cost of at most a few tenths of a percent per year if it is worth nothing.
"""IntelliStock v2 — index core with a bounded conviction tilt.

WHY THIS EXISTS
---------------
A ten-line research sweep on 2026-08-03 (see `reference_beat-spy-research`)
computed from primary data — Ken French's library, Cboe's official histories,
AQR's own factor files, and this repo's RethinkDB — and every line reached the
same verdict: **nothing a retail account TRADES has beaten SPY out of sample.**

    factor ETFs      every category lost over 10 AND 15 years
    momentum         UMD post-2005 +1.20%/yr, t=0.75; GEM -5.8pp/yr since
                     publication; Faber 10-month -6.3pp/yr since 2013
    leverage         never improves Sharpe; 3x ~= 56% chance of >50% loss/decade
    options          BXM/PUT are FRICTIONLESS indices and still lost 3.9-4.9pp/yr
    concentration    with no demonstrated skill it lowers the MEDIAN outcome
    Piotroski F      failed replication, t=1.06
    QMJ              ~60% post-publication decay; trailing 10yr -0.16%/yr

Against that, the honest prior for any active equity strategy is that it will
LOSE to SPY. So this strategy starts from SPY and asks the signal to earn its
way in, rather than starting from the signal and hoping.

There is exactly one thing this project has never tested. Across all seven
2026 backtest runs the graph produced `raw_score=0.000` in 677 of 677
conviction evaluations, and NEO4J_* credentials were never forwarded to live
instance containers (fixed 435e508). **Every "the Nexus signal is worthless"
conclusion was drawn about a system whose signal never ran.** That is not
evidence of absence, and it is the only unexplored hypothesis left.

This module is the cheap experiment. The core guarantees the book roughly
tracks the index; the satellite is capped so that if the graph is worthless the
whole thing costs a few tenths of a percent a year, and if it is real the A/B
against `core_target_pct=1.0` will say so.

WHAT IT DOES
------------
    core        `core_target_pct` of NAV in `core_symbol` (SPY), rebalanced only
                when drift exceeds `core_band_pct` AND `rebalance_min_days` have
                passed. Two gates, not one: a band alone still churns in chop.
    satellite   the remaining sleeve, equal-weighted across at most
                `satellite_max_names` names whose conviction score clears
                `satellite_min_score`. Equal-weight is deliberate — with no
                demonstrated selection skill, score-proportional weighting just
                concentrates into the least-reliable estimates.
    exits       a name is sold only when it falls out of the top
                `rank_band_exit_rank`, not when it leaves the top N. That gap is
                the "buy/hold spread" (Novy-Marx & Velikov's sS rule): stricter
                to ESTABLISH a position than to MAINTAIN one. It is the single
                most effective turnover mitigation in their taxonomy.

Turnover by construction is roughly `4 x satellite_max_names` round trips a
year, i.e. well under 100%/yr, where the measured 23.2 bps one-way costs about
0.23%/yr — a rounding error rather than the 8.07%/yr the live book pays today.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
No leverage, no inverse/bear leg, no shorting, no options, no market timing.
Each was researched and each failed on its own evidence; the bear leg in
particular cost 1.67pp on the bull window here and needed six independent
suppressors before it stopped losing money. Adding any of them would be
choosing a story over the measurements.

DEFAULT OFF. `enabled` is false, so attaching this strategy changes nothing
until an operator sets it.
"""

from __future__ import annotations

import datetime as _dt

#: A name must clear the entry cut to be BOUGHT, but only falls out of the
#: portfolio once it leaves this deeper rank. The hysteresis is the point.
DEFAULT_EXIT_RANK = 12

#: Never emit an order below this notional — Alpaca rejects sub-$1 orders and
#: the fee model still charges spread on dust.
MIN_ORDER_USD = 50.0


def _f(cfg, key, default):
    try:
        v = (cfg or {}).get(key, default)
        if v is None or v == "":
            return float(default)
        out = float(v)
        return out if out == out and abs(out) != float("inf") else float(default)
    except (TypeError, ValueError, AttributeError):
        return float(default)


def _i(cfg, key, default):
    try:
        v = (cfg or {}).get(key, default)
        if v is None or v == "":
            return int(default)
        return int(v)
    except (TypeError, ValueError, AttributeError):
        return int(default)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _as_utc(value):
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    try:
        parsed = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=_dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _days_between(then, now):
    a, b = _as_utc(then), _as_utc(now)
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 86400.0


def plan_targets(*, nav, core_symbol, core_value, core_target_pct,
                 core_band_pct, days_since_rebalance, rebalance_min_days,
                 ranked_symbols, held_symbols, satellite_max_names,
                 exit_rank):
    """Pure sizing rule. Returns ``(targets, notes)``.

    Split out from `run_once` so the arithmetic is unit-testable without a
    broker, an emulator or a database — the same reason `core_sleeve.py` exists
    as its own module.

    ``targets`` maps symbol -> desired fraction of NAV. ``notes`` explains each
    decision so an operator can see WHY the book looks the way it does rather
    than inferring it from absent trades.
    """
    notes = []
    targets: dict = {}
    nav = float(nav or 0.0)
    if nav <= 0:
        return targets, ["no_nav"]

    core_target_pct = max(0.0, min(1.0, float(core_target_pct)))
    satellite_budget = max(0.0, 1.0 - core_target_pct)

    # ── satellite membership, with the buy/hold spread ────────────────────
    #
    # A HELD name survives while it is inside `exit_rank`; a NEW name must be
    # inside `satellite_max_names`. Selling on "left the top N" would round-trip
    # every name that wobbles across the boundary, which is exactly the churn
    # this design exists to avoid.
    ranked = [str(s).strip().upper() for s in (ranked_symbols or []) if s]
    held = {str(s).strip().upper() for s in (held_symbols or []) if s}
    core_symbol = str(core_symbol or "SPY").strip().upper()
    held.discard(core_symbol)

    rank_of = {sym: i for i, sym in enumerate(ranked)}
    keep = [s for s in ranked[:max(0, int(exit_rank))] if s in held]
    room = max(0, int(satellite_max_names) - len(keep))
    adds = [s for s in ranked[:max(0, int(satellite_max_names))]
            if s not in held][:room]
    members = keep + adds

    for sym in sorted(held - set(members)):
        notes.append(f"exit {sym} (rank "
                     f"{rank_of.get(sym, 'unranked')} outside top {exit_rank})")
    for sym in adds:
        notes.append(f"enter {sym} (rank {rank_of.get(sym)})")

    if members:
        # Equal weight, NOT score-proportional. With no demonstrated selection
        # skill, weighting by score concentrates capital into the least
        # reliable estimates in the set.
        each = satellite_budget / float(len(members))
        for sym in members:
            targets[sym] = each
    else:
        notes.append("no satellite names clear the bar — core absorbs the sleeve")

    # ── core: whatever the satellite did not use ──────────────────────────
    #
    # The core is a RESIDUAL. That is what makes a satellite buy an active
    # OVERWEIGHT funded by selling the index, rather than a new net long.
    core_target = 1.0 - sum(targets.values())
    current_core_w = float(core_value or 0.0) / nav
    drift = core_target - current_core_w

    cadence_ok = (days_since_rebalance is None
                  or days_since_rebalance >= rebalance_min_days)
    if abs(drift) <= float(core_band_pct) and not adds and not (held - set(members)):
        notes.append(f"core within band ({current_core_w:.1%} vs "
                     f"{core_target:.1%}) — holding")
        targets.pop(core_symbol, None)
        return targets, notes
    if not cadence_ok and not adds and not (held - set(members)):
        notes.append(f"core drifted {drift:+.1%} but cadence holds "
                     f"({days_since_rebalance:.0f}d of {rebalance_min_days}d)")
        return targets, notes

    targets[core_symbol] = core_target
    notes.append(f"core -> {core_target:.1%} (from {current_core_w:.1%})")
    return targets, notes


class IndexCoreTilt:
    """Run-once strategy: SPY core plus a bounded conviction tilt.

    Returns the standard `{symbol: 1|0|-1}` contract. Sizing is expressed by
    emitting BUY only for names the book is under-weight in and SELL for names
    it should not hold, which is what the broker's run_once lane consumes.
    """

    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = config or {}
        if not _truthy(cfg.get("enabled", False)):
            return {}
        if portfolio_emulator is None:
            return {}

        core_symbol = str(cfg.get("core_symbol", "SPY")).strip().upper()
        prices = prices or {}
        try:
            nav = float(portfolio_emulator.get_portfolio_value(prices) or 0.0)
            positions = portfolio_emulator.get_positions() or {}
        except Exception:
            return {}
        if nav <= 0:
            return {}

        core_px = float(prices.get(core_symbol) or 0.0)
        if core_px <= 0:
            # No mark for the core: nothing here is decidable, and guessing
            # would rebalance against a stale price.
            return {}
        core_value = float(positions.get(core_symbol, 0.0) or 0.0) * core_px

        cache = strategy_cache if isinstance(strategy_cache, dict) else {}
        days_since = _days_between(cache.get("_ict_last_rebalance"), current_time)

        # Conviction ranking supplied by whatever upstream produced it. An
        # EMPTY or all-zero ranking is the documented graph-inert state; in
        # that case the strategy degrades to holding the core outright, which
        # is the correct behaviour rather than a failure.
        ranked = self._ranked_symbols(cfg, symbols, data, prices, core_symbol)

        targets, notes = plan_targets(
            nav=nav,
            core_symbol=core_symbol,
            core_value=core_value,
            core_target_pct=_f(cfg, "core_target_pct", 0.85),
            core_band_pct=_f(cfg, "core_band_pct", 0.05),
            days_since_rebalance=days_since,
            rebalance_min_days=_i(cfg, "rebalance_min_days", 90),
            ranked_symbols=ranked,
            held_symbols=[s for s in positions if positions.get(s)],
            satellite_max_names=_i(cfg, "satellite_max_names", 6),
            exit_rank=_i(cfg, "rank_band_exit_rank", DEFAULT_EXIT_RANK),
        )
        for note in notes:
            print(f"[IndexCoreTilt] {note}")

        decisions: dict = {}
        for sym, target_w in targets.items():
            px = float(prices.get(sym) or 0.0)
            if px <= 0:
                continue
            current = float(positions.get(sym, 0.0) or 0.0) * px
            delta = (target_w * nav) - current
            if abs(delta) < MIN_ORDER_USD:
                continue
            decisions[sym] = 1 if delta > 0 else -1
        # Anything held that the plan does not name is a full exit.
        for sym, qty in positions.items():
            s = str(sym).strip().upper()
            if qty and s not in targets and s != core_symbol:
                decisions.setdefault(s, -1)

        if decisions:
            cache["_ict_last_rebalance"] = current_time
        return decisions

    @staticmethod
    def _ranked_symbols(cfg, symbols, data, prices, core_symbol):
        """Conviction-ordered candidate list, best first.

        Reads `data["conviction_scores"]` when an upstream strategy provides
        one (this is how the graph would feed the tilt). Falls back to an EMPTY
        ranking — never to an arbitrary ordering, because a fake ranking would
        make a dead signal look alive and is precisely how the 677/677
        `raw_score=0.000` state stayed invisible for so long.
        """
        scores = {}
        if isinstance(data, dict):
            raw = data.get("conviction_scores") or {}
            if isinstance(raw, dict):
                for sym, val in raw.items():
                    try:
                        score = float(val)
                    except (TypeError, ValueError):
                        continue
                    s = str(sym).strip().upper()
                    if s and s != core_symbol and score > _f(
                            cfg, "satellite_min_score", 0.0):
                        scores[s] = score
        return [s for s, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]
