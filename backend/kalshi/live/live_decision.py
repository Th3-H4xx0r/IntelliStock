"""In-play decision (the live analog of the pre-match planner). Pure + tested.

Given the current position, the hybrid live fair value, and the live market
quotes, decide one of: open / add / reduce / exit / hold — under in-play risk
caps. Defensive actions (reduce/exit) are allowed in any phase; opening/adding
requires a live phase or a freshly detected price move (passed via allow_open)."""
from __future__ import annotations

from dataclasses import dataclass

from kalshi.live.match_clock import LIVE
from kalshi.risk import quarter_kelly_fraction


@dataclass(frozen=True)
class InPlayCaps:
    edge_threshold: float = 0.03
    kelly_fraction: float = 0.25
    bankroll_cents: int = 0
    max_contracts_per_market: int = 50
    inplay_exposure_frac: float = 0.15     # cap of bankroll deployed in-play per match
    max_adds_per_match: int = 2
    no_add_after_min: float = 75.0
    stop_loss_frac: float = 0.35           # DEPRECATED flat stop (kept for back-compat; no longer primary)
    take_profit_mult: float = 1.6          # DEPRECATED (replaced by fair-gated tp_overshoot_cents)
    catastrophe_stop_frac: float = 0.6     # last-resort exit if mark <= entry * (1 - this) AND losing late
    tp_overshoot_cents: float = 8.0        # take-profit: bank half when the MARK overshoots live fair by this
    late_exit_min: float = 70.0            # exits fire ONLY when the backed side is genuinely BEHIND this late
    maker_min_spread_cents: int = 3        # in-play maker-or-skip: only rest a maker when spread >= this
    maker_min_book_depth: int = 5          # require this much resting depth before making
    maker_max_adverse_imbalance: float = -0.5
    order_size_min_cents: int = 0          # order-size RANGE floor ($/order; 0/0 = auto/Kelly)
    order_size_max_cents: int = 0          # range ceiling — sized within [min,max] by edge conviction


@dataclass(frozen=True)
class LiveAction:
    kind: str          # open | add | reduce | exit | hold
    contracts: int
    reason: str


def _implied(cents) -> float:
    return max(0.0, min(1.0, (float(cents) or 0.0) / 100.0))


def _size(edge: float, ask_cents: float, caps: InPlayCaps, *, held_contracts: int) -> int:
    """Contracts at the live ask, clamped by per-market + in-play exposure caps (counting
    what's already held). If an order-size RANGE [min,max] is set, size within it by edge
    conviction; else quarter-Kelly of the bankroll."""
    if ask_cents <= 0:
        return 0
    _osmax = int(getattr(caps, "order_size_max_cents", 0))
    if _osmax > 0:
        _osmin = int(getattr(caps, "order_size_min_cents", 0))
        _lo, _hi = min(_osmin, _osmax), max(_osmin, _osmax)
        _conv = max(0.0, min(1.0, float(edge) / 0.20))
        stake_cents = _lo + (_hi - _lo) * _conv
        if stake_cents <= 0:
            return 0
    else:
        frac = quarter_kelly_fraction(edge=edge, price_cents=ask_cents, kelly=caps.kelly_fraction)
        if frac <= 0.0 or caps.bankroll_cents <= 0:
            return 0
        stake_cents = frac * caps.bankroll_cents
    n = int(stake_cents // ask_cents)
    room_market = max(0, caps.max_contracts_per_market - held_contracts)
    exposure_cap_contracts = int((caps.inplay_exposure_frac * caps.bankroll_cents) // ask_cents)
    room_exposure = max(0, exposure_cap_contracts - held_contracts)
    return max(0, min(n, room_market, room_exposure))


def decide(*, position, live_fair: float, yes_ask_cents: float, yes_bid_cents: float,
           caps: InPlayCaps, phase: str, elapsed_min, adds_so_far: int = 0,
           allow_open: bool = True, origin: str = "inplay", our_goal_margin=None) -> LiveAction:
    """position: a KalshiContractPosition-like object (.contracts, .avg_price_cents,
    .current_price_cents) or None. Returns a LiveAction.

    origin: 'pregame' (a pre-match conviction bet) or 'inplay' (an in-play scalp).
    our_goal_margin: goals the side this market backs is currently ahead by (negative =
      behind), or None if the score isn't known — the score signal that gates exits.

    Priority: score-gated exit first, then (in-play only) value add/open, else hold.
    EXITS NEVER FIRE ON A PRICE MOVE ALONE — only when the scoreboard confirms the backed
    side is genuinely behind, late. Pregame bets are held to settlement (no scalp/TP)."""
    held = int(getattr(position, "contracts", 0) or 0) if position else 0
    fair = max(0.0, min(1.0, float(live_fair)))

    if held > 0:
        entry = float(getattr(position, "avg_price_cents", 0.0) or 0.0)
        mark = float(getattr(position, "current_price_cents", None) or yes_bid_cents or 0.0)
        is_pregame = (origin == "pregame")
        # Take-profit: IN-PLAY scalps only. A pregame conviction bet is held to settlement —
        # we never scalp out of a rising favorite we picked pre-match.
        if (not is_pregame and held >= 1 and entry > 0 and fair >= entry / 100.0
                and _implied(mark) >= fair + (caps.tp_overshoot_cents / 100.0)):
            sell = max(1, held // 2) if held >= 2 else 1
            return LiveAction("reduce", sell,
                              f"take-profit: mark {mark:.0f}c overshoots fair {fair:.2f} (thesis intact)")
        # EXITS ARE SCORE-GATED. Soccer prices gap hard on a goal but the fundamental thesis
        # usually survives, so we NEVER stop out on a price move alone (that dumped 'Spain to
        # win' at 19c after buying at 51c — and Spain won). An exit fires only when the
        # SCOREBOARD confirms the backed side is genuinely BEHIND, LATE (our_goal_margin < 0
        # at/after late_exit_min). Unknown score -> hold: don't crystallise a recoverable dip.
        losing_late = (our_goal_margin is not None and our_goal_margin < 0
                       and elapsed_min is not None and elapsed_min >= caps.late_exit_min)
        if losing_late:
            _behind = f"behind {our_goal_margin} @ {elapsed_min:.0f}'"
            # thesis break: model's live fair now sits below the bid by our edge margin.
            if fair + caps.edge_threshold < _implied(yes_bid_cents):
                return LiveAction("exit", held, f"thesis break ({_behind}): live fair {fair:.2f} << bid {_implied(yes_bid_cents):.2f}")
            # catastrophe backstop: mark collapsed to <= (1-frac) of entry AND losing late.
            if entry > 0 and mark <= entry * (1.0 - caps.catastrophe_stop_frac):
                return LiveAction("exit", held, f"catastrophe stop ({_behind}): mark {mark:.0f}c <= entry {entry:.0f}c·(1-{caps.catastrophe_stop_frac:.0%})")
        # Pregame conviction bets take no other in-play action — held to settlement.
        if is_pregame:
            return LiveAction("hold", 0, "pregame position — held to settlement (no in-play scalp)")

    # --- value: add (holding) or open (flat) ---
    ask_prob = _implied(yes_ask_cents)
    edge = fair - ask_prob
    can_act_now = (phase == LIVE) or allow_open
    if edge > caps.edge_threshold and can_act_now:
        if elapsed_min is not None and elapsed_min >= caps.no_add_after_min:
            return LiveAction("hold", 0, f"past no-add minute ({elapsed_min:.0f}>={caps.no_add_after_min:.0f})")
        if held > 0:
            if adds_so_far >= caps.max_adds_per_match:
                return LiveAction("hold", 0, f"max adds reached ({adds_so_far}/{caps.max_adds_per_match})")
            n = _size(edge, yes_ask_cents, caps, held_contracts=held)
            if n > 0:
                return LiveAction("add", n, f"value +{edge:.2f}: live fair {fair:.2f} vs ask {ask_prob:.2f}")
        elif allow_open:
            n = _size(edge, yes_ask_cents, caps, held_contracts=0)
            if n > 0:
                return LiveAction("open", n, f"value +{edge:.2f}: live fair {fair:.2f} vs ask {ask_prob:.2f}")

    return LiveAction("hold", 0, f"no in-play action (edge {edge:+.2f}, phase {phase})")
