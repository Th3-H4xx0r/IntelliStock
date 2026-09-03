"""Strategy EB sizing — a vol-targeted levered Nasdaq core with a SPY/BIL tail.

Design: docs/superpowers/specs/2026-08-27-strategy-eb-design.md
Research: docs/superpowers/research/2026-08-27-all-regime-research.md

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
It is a RISK TRANSFORM, not an alpha. Nine pre-registered signal tests on this
universe returned nine KILLs; the one construction that survived is leverage
efficiency, worth ~+2.6pp CAGR at MATCHED maximum drawdown against a static
levered blend (49 of 52 configurations, same sign in both halves of 2010-2026).
The strategy makes no directional prediction. It holds LESS of the same position
when that position is more dangerous.

Measured locally (yfinance 2010-2026, 4.4 bps on ETF legs, next-bar fills):
    CAGR ~24%, max drawdown ~-40%, one-way turnover ~250-300%/yr.
Every local harness in this repo over-states. The engine is the verdict.

WHAT IT DELIBERATELY DOES NOT CONTAIN, each with measured evidence
------------------------------------------------------------------
  * No MA200 gate           - below the always-long base rate.
  * No inverse leg          - the bottom detector fails (n=77, t -0.95).
  * No binary drawdown halt - fits the one bear in the window.
  * No slow/fast trend gate - a wash: -0.4pp CAGR for +99%/yr turnover.
  * No commodity / managed-futures / gold sleeve - all KILLed.
  * No Graph Nexus sleeve   - zero measured cross-sectional signal, and
                              untestable in any bear in the engine window.

Pure: no clock, no RNG, no I/O. `broker.py` is not import-safe (argparse at
module scope SystemExits under pytest), so anything testable lives here.

2026-08-31 — champion + ceiling (docs/superpowers/research/
2026-08-31-strategy-eb-champion.md). Eight search iterations, ~547k configs.
Best ENGINE config (tv .20 / 10,40 / wmax .65 / QQQ / trend_filter_bars 25 /
books both GLD .5 GDX .25 XLE .25) returned +233.8% vs SPY-TR +77.1%, maxDD
-27.5%, all three bears >= 0 — and FAILED the frozen section-11 gate on G5,
474%/yr turnover against a 400% bound. 95% rolling-12m is unreachable here
(three walls, measured). The margin is a gold/energy era bet: 2010-2021 the
same construction LOSES to SPY. Module DEFAULTS stay off (empty book ==
the legacy two-leg remainder, and the tests pin that contract); the SHIPPED
config in the wrapper's INTELLISTOCK_SCHEMA is the bil25 variant adopted
2026-08-31: champion books scaled so 25% of the risk-off remainder falls
through to BIL (trend_off_book GLD .375 GDX .1875 XLE .1875,
risk_off_symbol BIL). Engine card: +197.8% vs SPY-TR +77.1%, maxDD -21.1%
vs SPY's -24.7% — the champion's own -27.5% tail was a concentrated gold
break, and each ~1pp of drawdown bought back costs ~7pp of cycle return.
"""
from __future__ import annotations

import math

from strategy_x import Q, _finite, _stdev

__all__ = [
    "DEFAULTS", "LAST_REBALANCE_KEY", "LAST_STATE_KEY", "eb_core_weight",
    "eb_remainder_targets", "eb_should_trade", "eb_state_book", "eb_targets",
    "eb_trend_enabled", "eb_trend_state", "rebalance_weekdays",
    "session_ordinal", "session_weekday", "strategy_eb_universe",
]


#: Days since this epoch, not the proleptic ordinal (~739,000 today), so a
#: session counter stays inside the 100,000 bound the bear module applies to
#: every parsed counter. Same constant as strategies/strategy_x.py:284.
_SESSION_EPOCH_ORDINAL = 719163  # date(1970, 1, 1).toordinal()

#: 1970-01-01 was a THURSDAY, i.e. weekday() == 3. So Monday-based weekday is
#: (days_since_epoch + 3) % 7, with no second date parse.
_EPOCH_WEEKDAY = 3

#: Where the wrapper records the session it last traded in, so intraday
#: granularity cannot produce a second rebalance in the same session.
LAST_REBALANCE_KEY = "_eb_last_rebalance_session"

#: Where the wrapper records the trend state the last EXECUTED rebalance was
#: built in. `eb_should_trade` reads it: the band is measured on the core
#: weight, and a state flip changes only the REMAINDER, which the band cannot
#: see. Absent — the default, and every run with the filter off — the clause is
#: inert and the band alone decides.
LAST_STATE_KEY = "_strategy_eb_last_state"

_TRADING_DAYS = 252


DEFAULTS = {
    "strategy_eb_enabled": False,
    # ── the legs ──
    # TQQQ at 3x rather than QLD at 2x: the volatility drag depends on TOTAL
    # exposure m = k*w, not on the fund's multiple, so 40% TQQQ and 60% QLD are
    # the same beta on paper — and the 3x fund reaches it with a third of the
    # capital, leaving the remainder in SPY instead of idle. `core_leverage`
    # must match the fund: it is the divisor in the vol target, and setting
    # 3.0 against a 2x fund would size to two-thirds of the intended beta.
    "core_symbol": "TQQQ",
    "core_leverage": 3.0,
    # The vol is measured on the UNLEVERED index. Measuring it on TQQQ itself
    # would divide a 3x-inflated vol by the 3x leverage a second time.
    "reference_symbol": "QQQ",
    "off_symbol": "SPY",
    "cash_symbol": "BIL",
    # ── the transform ──
    # 0.20 annualised on the whole book. Raising it is the single most
    # dangerous edit in this file: exposure is linear in it.
    "target_vol": 0.20,
    # The clamp, not the vol target, is what bounds the worst case. At 0.65 of
    # a 3x fund the book carries 195% Nasdaq beta on the calmest tape in the
    # sample, which is where the ~-40% local maximum drawdown comes from.
    "core_max_weight": 0.65,
    # Quantization is the turnover control. Unquantized daily vol-scaling of a
    # 3x leg measured 1,000-2,000%/yr turnover in the Strategy X work; a 0.05
    # grid plus the band below brings it to 207-299%/yr. FLOORING (never
    # rounding) means quantization can only ever hold LESS.
    "weight_step": 0.05,
    "vol_fast_bars": 20,
    "vol_slow_bars": 60,
    # 70 closes = 69 returns, enough for the 60-bar slow window. Below this the
    # strategy returns {} and logs red. A cold start must never lever up.
    "min_history_bars": 70,
    # ── cadence ──
    # 0.10 of NAV. The band is what makes a weekly cadence a weekly TRADE
    # count rather than a weekly evaluation: most Wednesdays the drift is
    # inside it and nothing is sent.
    "core_rebalance_band": 0.10,
    # NY weekday of the LAST VISIBLE SESSION, Monday=0 — not of the call.
    # `pit_daily_observations` returns strictly-earlier sessions, so [2]
    # (Wednesday) means "decide on the first call that can see Wednesday's
    # close", which at daily granularity is Thursday's call. One tranche. Two
    # entries (e.g. [1, 3]) moves half the way on each and removes
    # rebalance-timing luck, which is worth >100 bp/yr — at the cost of
    # doubling order count, which is why it is not the default on a $6k
    # account.
    "rebalance_weekdays": [2],
    # THE DIAL. 0.0 = the whole de-levered remainder in SPY (approach A);
    # 1.0 = the whole remainder in T-bills (approach B), which costs ~8pp of
    # CAGR and takes 2022 from about -30% to about -12%. Anything between is
    # a linear blend. With weight >= 0 and a SPY remainder, a 2022 above SPY's
    # own -18% is impossible by construction; this key is the honest answer.
    "remainder_bil_fraction": 0.0,
    # ── the trend-conditioned remainder (ALL of it default-OFF) ──
    # SMA length on the REFERENCE symbol's point-in-time closes. 0 is the
    # feature switch, not a degenerate window: with it off every state read is
    # ON, the occupant is `off_symbol`, the damp never applies and the risk-off
    # leg is not even declared to the broker. The replay's grid found N=100 the
    # only length that wins the 2025-11 chop window; 150 and 200 lose it
    # outright.
    "trend_filter_bars": 0,
    # Hysteresis, and it is deliberately ASYMMETRIC. ON -> OFF when the
    # close is below SMA*(1 - enter); OFF -> ON only when it is above
    # SMA*(1 + exit).
    # The dead zone between them is the whole defence against whipsaw: at 1%/2%
    # the replay still flipped 10 times in 2011 and lost 30pp to a flat SPY.
    # Narrowing them is the single most dangerous edit in this block.
    "trend_off_enter_pct": 0.01,
    "trend_on_exit_pct": 0.02,
    # Occupant of the whole remainder while the state is OFF. "" means the
    # cash leg, which is the T-bill variant of the replay grid. Setting it to
    # GLD is the entire measured margin of the trend feature AND its entire
    # risk: across 46 risk-off episodes gold beat SPY in 23 — a coin flip whose
    # mean is carried by one 2008 episode. Enabling this is a bet on a hedge
    # with no statistically significant conditional edge.
    "risk_off_symbol": "",
    # The core is multiplied by this while OFF, BEFORE the clamp and the 0.05
    # quantisation — the replay computes w = clip(tv/(k*rv) * damp, 0, cap), so
    # on a tape calm enough for the clamp to bind a 0.5 damp changes nothing.
    # 1.0 keeps the full core and only rotates the remainder; 0.0 leaves the
    # levered fund entirely. Over 2010-2021 cutting the core bought ZERO
    # drawdown protection (all variants hit -38.5%, set by a COVID crash too
    # fast for a weekly SMA) and cost 4.6pp/yr of CAGR, which is why the
    # default damps nothing.
    "core_off_damp": 1.0,
    # ── the remainder BOOKS (default-off: an empty book is no book) ──
    # {SYMBOL: weight} the de-levered remainder is split across INSTEAD of the
    # single occupant, one book per state. Weights are shares of the remainder,
    # not of NAV: at a 0.40 core, {"SMH": 0.3, "GLD": 0.7} is 18% SMH and 42%
    # GLD. They may sum to less than 1 — the shortfall goes to the occupant the
    # state would have used on its own (the SPY/BIL dial while ON, the risk-off
    # or cash leg while OFF), so an EMPTY book is exactly the two-leg book that
    # existed before this key. A book summing past 1 is RENORMALISED rather
    # than clipped: clipping would silently drop whichever leg came last.
    #
    # `trend_on_book` is read in the ON state, which with `trend_filter_bars=0`
    # is EVERY state — that is the static-blend configuration: a fixed
    # multi-ETF remainder with no state machine at all. `trend_off_book` needs
    # the filter ON to be reachable, and like `risk_off_symbol` it is not even
    # declared to the broker otherwise.
    #
    # Set with `target_vol: 0` (or `core_max_weight: 0`) this is a PURE BOOK:
    # core weight 0, the book carries the whole NAV. That is the one config in
    # which the levered fund is absent by design rather than by refusal.
    "trend_on_book": {},
    "trend_off_book": {},
    # ── the cash sweep ──
    # `targets_to_orders` sizes buys off SETTLED cash and equity fills are
    # next-bar, so the tick that sells the core CANNOT also fund the remainder
    # leg — that buy is clipped to whatever cash had already settled. Left
    # alone the freed cash is never deployed: the band sees no breach on any
    # later session, and after a full exit the cadence rule returns
    # (False, 0.0), so the book drifts to cash and stays there. Above this
    # fraction of NAV, an idle balance is re-offered to the REMAINDER legs on
    # any tick that is not already sending a core order. Below it, sweeping is
    # pure churn.
    "cash_sweep_min_pct": 0.02,
    # ── sharing a document with another lane ──
    # Fraction of NAV this book LEAVES to a sibling run_once lane (the outlier
    # sleeve, 2026-09-02). 0.0 = the whole account is this book, byte-for-byte
    # the behaviour before the key existed. Above 0 the book is sized off
    # NAV minus max(reserve, value of positions outside this universe), and
    # the sibling's still-undeployed share is held back from every buy and
    # from the sweep. Measured without it: the sweep took every settled
    # dollar and the sleeve made 0.25 entries per screen (bt 876989).
    "reserve_for_other_lanes_pct": 0.0,
    # ── execution (read by strategy_x.targets_to_orders) ──
    "core_band_pct": 0.03,
    "min_order_usd": 25.0,
    "cost_haircut_pct": 0.005,
    # ── broker-side keys, read by backtest_engine, not by this module ──
    # The broker trims ANY single position to BROKER_MAX_SINGLE_POSITION_PCT
    # (default 0.15) and trims the buy to ZERO rather than clipping it. A
    # 65%-of-NAV core cannot be built underneath it: on BT102936 every levered
    # buy logged "trimmed to $0.00 ... cap=15%".
    "broker_max_single_position_pct": 0.95,
    "honour_single_position_cap": True,
    # ── live risk envelope, read by broker.py for THIS document only ──
    # A strategy designed to ride a -30% drawdown cannot live under the module
    # default 5% soft buy-freeze, and a 65% core cannot be built under a 10%
    # per-order cap. The gate keeps BLOCKING, never clipping; the caps are
    # simply set to what this strategy asks for. Every other document keeps
    # live_risk_state's module defaults untouched.
    "live_max_order_fraction": 0.70,
    "live_max_symbol_fraction": 0.70,
    "live_max_leveraged_fraction": 0.70,
    "live_soft_drawdown": 0.25,
    "live_hard_drawdown": 0.35,
    "live_kill_drawdown": 0.45,
}


# Own parsers rather than strategy_x's, for two measured reasons. Its `_i`
# raises OverflowError on float("inf") — `int(inf)` is not caught by its
# (TypeError, ValueError, AttributeError) — and it resolves a missing default
# against strategy_x's DEFAULTS, so any EB-only key without an explicit default
# raises TypeError. Both fail OPEN, which is the wrong direction for a parser
# guarding a levered position.
def _f(cfg, key, default=None):
    if default is None:
        default = DEFAULTS.get(key, 0.0)
    try:
        value = (cfg or {}).get(key, default)
        if value is None or value == "":
            return float(default)
        out = float(value)
    except (TypeError, ValueError, AttributeError, OverflowError):
        return float(default)
    return out if math.isfinite(out) else float(default)


def _i(cfg, key, default=None):
    if default is None:
        default = DEFAULTS.get(key, 0)
    try:
        value = (cfg or {}).get(key, default)
        if value is None or value == "":
            return int(default)
        if isinstance(value, float) and not math.isfinite(value):
            return int(default)
        return int(value)
    except (TypeError, ValueError, AttributeError, OverflowError):
        return int(default)


def _s(cfg, key, default=None):
    if default is None:
        default = DEFAULTS.get(key, "")
    value = (cfg or {}).get(key, default)
    return str(value if value is not None else default).strip().upper()


def _state(value) -> str:
    """Any cache value as a state. Anything unrecognised — None, "", a
    corrupted row — reads as ON, the SAME answer a cold start gives, so a
    damaged cache cannot produce a third behaviour."""
    return "OFF" if str(value or "").strip().upper() == "OFF" else "ON"


def eb_trend_enabled(cfg) -> bool:
    """Whether the trend-conditioned remainder is switched on at all.

    One predicate, read by the universe, the wrapper and the sweep, so "off"
    means the same thing in all three. With it False the strategy is the
    two-leg book it was before this feature existed, byte for byte.
    """
    return _i(cfg, "trend_filter_bars") > 0


def eb_trend_state(closes, prev_state, cfg) -> str:
    """"ON" or "OFF" — which asset the de-levered remainder belongs in.

        sma  = mean(closes[-N:])
        ON  -> OFF  when close < sma * (1 - trend_off_enter_pct)
        OFF -> ON   when close > sma * (1 + trend_on_exit_pct)

    Nothing else moves it. The asymmetric thresholds leave a dead zone that
    NEITHER edge can cross, which is what stops a weekly cadence whipsawing on
    a tape sitting on its own average.

    Fail-closed here means fail UNCHANGED: a short history or a non-finite
    close returns `prev_state`, never a guess. Inventing a state on a cold
    start is what would make a restart rotate the entire remainder.

    The CALLER decides when to ask. The replay evaluates this on decision
    weekdays only; evaluating it every session is a twitchier state path with
    more flips and more turnover, and turnover is already this design's
    binding constraint.
    """
    cfg = cfg or {}
    prev = _state(prev_state)
    if not eb_trend_enabled(cfg):
        # Not merely "start ON": with the feature off the machine must be
        # INCAPABLE of being off, or a stale persisted OFF from an earlier
        # config would rotate a default book out of SPY.
        return "ON"

    bars = _i(cfg, "trend_filter_bars")
    prices = _finite(closes)
    if prices is None or bars < 1 or bars > len(prices):
        return prev
    window = prices[-bars:]
    sma = sum(window) / len(window)
    if not math.isfinite(sma) or sma <= 0:
        return prev

    close = prices[-1]
    if prev == "ON":
        enter = max(0.0, min(1.0, _f(cfg, "trend_off_enter_pct")))
        return "OFF" if close < sma * (1.0 - enter) else "ON"
    exit_pct = max(0.0, _f(cfg, "trend_on_exit_pct"))
    return "ON" if close > sma * (1.0 + exit_pct) else "OFF"


def eb_state_book(cfg, trend_state="ON") -> dict:
    """The remainder book for this state, cleaned: {SYMBOL: share}, share > 0.

    An unusable book — absent, not a dict, empty, or every leg dropped — is
    `{}`, and `{}` means "no book", which is the pre-feature single-occupant
    path. There is deliberately no third answer: a book that half-parses would
    put an arbitrary fraction of NAV somewhere nobody configured.

    Symbols are upper-cased and duplicates accumulate. Non-positive and
    unparseable weights are DROPPED rather than floored to zero — this book is
    long-only, and the weights are shares of a remainder that is already >= 0.

    Shares summing past 1 are renormalised so they sum to exactly 1. Shares
    summing to less than 1 are left alone: the shortfall is the caller's, and
    it belongs to the single occupant.
    """
    key = ("trend_off_book" if _state(trend_state) == "OFF"
           else "trend_on_book")
    raw = (cfg or {}).get(key)
    if not isinstance(raw, dict) or not raw:
        return {}

    out: dict = {}
    for symbol, weight in raw.items():
        name = str(symbol or "").strip().upper()
        if not name:
            continue
        try:
            share = float(weight)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(share) or share <= 0:
            continue
        out[name] = round(out.get(name, 0.0) + share, Q)

    total = sum(out.values())
    if not math.isfinite(total) or total <= 0:
        return {}
    if total > 1.0:
        out = {name: share / total for name, share in out.items()}
    return out


def _books_configured(cfg) -> bool:
    """Whether ANY reachable book is configured — the "this is a book strategy"
    predicate.

    Reachable is the operative word, and it is the same rule the universe
    applies: with the filter off the OFF book can never be read, so a config
    that sets only that one is not a book strategy and must keep behaving
    exactly as it did before these keys existed.

    Deliberately EITHER state, not the current one: a pure book that names only
    an ON book still has to be able to sell it and sit in the risk-off occupant
    when the state flips, and that rotation is decided in the OFF state, where
    the current book is empty.
    """
    if eb_state_book(cfg, "ON"):
        return True
    return bool(eb_trend_enabled(cfg) and eb_state_book(cfg, "OFF"))


def _book_legs(remainder: float, book: dict) -> tuple:
    """([(symbol, weight)], shortfall) for splitting `remainder` across `book`.

    The shortfall is what the book's shares leave unspent, and it is what the
    single occupant gets. Rounding each leg to Q can overshoot the remainder by
    a few 1e-7 on a renormalised book; that overshoot is taken back off the
    LARGEST leg, so the emitted weights still sum to exactly the remainder and
    a target set can never ask for more than the account holds.
    """
    legs = [(symbol, round(remainder * share, Q))
            for symbol, share in book.items()]
    shortfall = remainder - sum(weight for _, weight in legs)
    if shortfall < 0 and legs:
        biggest = max(range(len(legs)), key=lambda i: (legs[i][1],
                                                       legs[i][0]))
        symbol, weight = legs[biggest]
        legs[biggest] = (symbol, round(weight + shortfall, Q))
        shortfall = 0.0
    return legs, shortfall


def session_ordinal(session_id) -> int:
    """A monotonic integer per NY session, or 0 when the label is unusable.

    Derived from the session DATE rather than a bar count: bar buffers get
    trimmed, and a clock that ran backwards whenever the cache was trimmed
    would read as corruption.
    """
    from datetime import date as _date

    try:
        return max(0, _date.fromisoformat(str(session_id)).toordinal()
                   - _SESSION_EPOCH_ORDINAL)
    except (TypeError, ValueError):
        return 0


def session_weekday(session_id) -> int:
    """NY weekday of a session label, Monday=0, or -1 when unusable.

    From the session ordinal, never from a call count: at 15m granularity
    run_once fires ~26 times a session and a holiday row repeats the last
    completed close.
    """
    ordinal = session_ordinal(session_id)
    if ordinal <= 0 and str(session_id) != "1970-01-01":
        return -1
    return (ordinal + _EPOCH_WEEKDAY) % 7


def rebalance_weekdays(cfg) -> tuple:
    """Configured decision weekdays, sorted and de-duplicated.

    Falls back to the default rather than to "every day": a malformed list must
    never turn a weekly strategy into a daily one, which is the turnover
    failure this whole design exists to avoid.
    """
    raw = (cfg or {}).get("rebalance_weekdays", DEFAULTS["rebalance_weekdays"])
    out = set()
    try:
        for value in (raw or []):
            if isinstance(value, bool):
                continue
            day = int(value)
            if 0 <= day <= 6:
                out.add(day)
    except (TypeError, ValueError, AttributeError, OverflowError):
        out = set()
    if not out:
        return tuple(DEFAULTS["rebalance_weekdays"])
    return tuple(sorted(out))


def _quantize_floor(value: float, step: float) -> float:
    """Floor `value` onto a `step` grid. Rounding to 9 dp first is load-bearing:
    0.65 / 0.05 is 12.999999999999998 in binary floating point on some inputs,
    and a bare floor would silently hold 0.60 whenever the clamp bound."""
    if step <= 0:
        return value
    return round(math.floor(round(value / step, 9)) * step, Q)


def eb_core_weight(closes, cfg, trend_state="ON") -> float | None:
    """Target core weight as a fraction of NAV, or None to REFUSE.

        rv    = max(stdev(ret, 20), stdev(ret, 60)) * sqrt(252)
        w_raw = target_vol / (leverage * rv) * (core_off_damp if OFF else 1)
        w     = floor(clamp(w_raw, 0, core_max_weight) / step) * step

    The damp multiplies the RAW weight — before the clamp and before the grid,
    exactly as the replay computes it. Order matters twice over: damping a
    quantised 0.40 by 0.9 gives 0.36, which is off the 0.05 grid the turnover
    control depends on; and on a tape calm enough for the clamp to bind, a
    halved 4.09 still clamps to 0.65, so the damp correctly does nothing there.
    Clamping first would have made that case a 0.30 position — a different
    strategy from the one whose numbers were measured.

    None means "the strategy cannot evaluate its own risk". The caller must
    return {} — NOT fall back to a default weight. Every failure mode here
    (short history, a NaN close, a flat tape, a zero leverage) would otherwise
    resolve to MORE leverage, not less.
    """
    prices = _finite(closes)
    # The floor is whichever is LARGER: the configured minimum, or one more
    # close than the longest vol window needs. Without the window terms a
    # `vol_slow_bars` above `min_history_bars` silently truncates the slow
    # window to whatever history happens to exist — a SHORTER window on the
    # same tape measures LESS risk, and less measured risk sizes the 3x core
    # LARGER. That is the one direction this module must never fail in.
    minimum = max(2, _i(cfg, "min_history_bars"),
                  _i(cfg, "vol_fast_bars") + 1, _i(cfg, "vol_slow_bars") + 1)
    if prices is None or len(prices) < minimum:
        return None

    leverage = _f(cfg, "core_leverage")
    if not math.isfinite(leverage) or leverage <= 0:
        return None

    returns = [prices[i + 1] / prices[i] - 1.0 for i in range(len(prices) - 1)]
    fast = max(2, _i(cfg, "vol_fast_bars"))
    slow = max(2, _i(cfg, "vol_slow_bars"))
    if len(returns) < 2:
        return None
    rv = max(_stdev(returns[-fast:]), _stdev(returns[-slow:]))
    rv *= math.sqrt(_TRADING_DAYS)
    if not math.isfinite(rv) or rv <= 0:
        return None

    target_vol = max(0.0, _f(cfg, "target_vol"))
    cap = max(0.0, min(1.0, _f(cfg, "core_max_weight")))
    step = _f(cfg, "weight_step")
    if not math.isfinite(step) or step <= 0:
        return None

    w_raw = target_vol / (leverage * rv)
    if _state(trend_state) == "OFF":
        # Clamped to [0, 1]. A damp above 1 is a config error that would size
        # the 3x core LARGER in the one state this feature exists to de-risk —
        # the only direction this key must never move.
        w_raw *= max(0.0, min(1.0, _f(cfg, "core_off_damp")))
    if not math.isfinite(w_raw):
        return None
    return _quantize_floor(max(0.0, min(cap, w_raw)), step)


def eb_targets(w, cfg, trend_state="ON") -> dict:
    """Target weight per symbol as a fraction of NAV. Sums to exactly 1.0.

    Risk-ON (and every call that does not pass a state, which is every caller
    with the filter off):

        core = w
        bil  = (1 - w) * remainder_bil_fraction
        spy  = 1 - core - bil

    Risk-OFF: the whole remainder goes to `risk_off_symbol`, or to the cash leg
    when that is unset — the T-bill variant of the replay grid, not a
    misconfiguration. The BIL dial does not apply there because there is no
    SPY leg left to blend against.

    With a BOOK configured for the state (`trend_on_book` / `trend_off_book`),
    the remainder is split across the book's symbols by share and only the
    SHORTFALL reaches the single occupant above. An empty book — the default —
    is a shortfall of the whole remainder, which is why the two formulas above
    are still the whole of the default behaviour, arithmetic included.

    The remainder is computed as the RESIDUAL rather than as `(1-w)*(1-dial)`
    so the legs sum to 1.0 at Q decimals by construction. A weight set summing
    past 1.0 asks for a clip the account cannot fund.
    """
    cfg = cfg or {}
    try:
        core = float(w)
    except (TypeError, ValueError):
        core = 0.0
    if not math.isfinite(core):
        core = 0.0
    core = round(max(0.0, min(1.0, core)), Q)

    # With no book this is `1.0 - core` unchanged, so every expression below is
    # the arithmetic that shipped before the books existed, to the last bit.
    book_legs, rest = _book_legs(1.0 - core, eb_state_book(cfg, trend_state))

    if _state(trend_state) == "OFF":
        occupant = _s(cfg, "risk_off_symbol") or _s(cfg, "cash_symbol")
        legs = ((_s(cfg, "core_symbol"), core),
                (occupant, round(rest, Q)))
    else:
        dial = max(0.0, min(1.0, _f(cfg, "remainder_bil_fraction")))
        bil = round(rest * dial, Q)
        spy = round(rest - bil, Q)
        legs = ((_s(cfg, "core_symbol"), core),
                (_s(cfg, "cash_symbol"), bil),
                (_s(cfg, "off_symbol"), spy))
    legs = tuple(legs) + tuple(book_legs)

    targets: dict = {}
    for symbol, weight in legs:
        if symbol and weight > 0:
            targets[symbol] = round(targets.get(symbol, 0.0) + weight, Q)
    return targets


def eb_remainder_targets(w_held, cfg, trend_state="ON") -> dict:
    """Target weights for the REMAINDER legs only, around the core ALREADY held.

        bil = (1 - w_held) * remainder_bil_fraction
        spy = 1 - w_held - bil

    or, with a book configured for the state, that same `1 - w_held` split
    across the book — the sweep funds the occupants the CURRENT state names,
    never the ones the last plan happened to name.

    The core is REMOVED rather than targeted at zero: a target of zero is an
    exit instruction to `targets_to_orders`, so leaving it in would liquidate
    the core every time the sweep ran. Its absence from both the targets and the
    `owned` scope is what keeps the sweep incapable of touching it — it can
    neither trim the core to fund the remainder nor top it up outside the weekly
    cadence.

    Sums to `1 - w_held`, never past it: asking for more would fund the buy by
    selling the very position the weight was measured against.
    """
    cfg = cfg or {}
    targets = eb_targets(w_held, cfg, trend_state)
    targets.pop(_s(cfg, "core_symbol"), None)
    return targets


def strategy_eb_universe(cfg) -> list:
    """Every symbol this strategy reads or trades, deterministic order.

    The strategy owns its universe rather than depending on the instance's
    watchlist. Without this the reference symbol has no bars and the traded legs
    have no price, and BOTH failures are silent — the strategy simply emits
    nothing. `broker._strategy_eb_universe_symbols` reads this to decide what to
    fetch and what to price.
    """
    cfg = cfg or {}
    out: list = []
    keys = ["reference_symbol", "core_symbol", "off_symbol", "cash_symbol"]
    # Only when the filter is on. Declaring the risk-off leg otherwise makes
    # the broker fetch bars and carry a price for an asset that can never be
    # bought — and appended LAST, so enabling the filter cannot reorder the
    # four legs every existing run already declares.
    if eb_trend_enabled(cfg):
        keys.append("risk_off_symbol")
    for key in keys:
        symbol = _s(cfg, key)
        if symbol and symbol not in out:
            out.append(symbol)
    # The book legs LAST, in configured order, so adding a book cannot reorder
    # the four legs every existing run already declares. The OFF book is
    # declared only when the filter is on, for the same reason the risk-off leg
    # is: with the filter off the state is always ON and an OFF-book leg can
    # never be bought.
    books = [eb_state_book(cfg, "ON")]
    if eb_trend_enabled(cfg):
        books.append(eb_state_book(cfg, "OFF"))
    for book in books:
        for symbol in book:
            if symbol and symbol not in out:
                out.append(symbol)
    return out


def eb_should_trade(session_id, w_target, w_held, cfg, cache,
                    trend_state="ON") -> tuple:
    """(trade?, core weight to move to) for this session.

    Read-only on `cache`. The CALLER writes `LAST_REBALANCE_KEY` after it has
    actually decided, so a refusal here never consumes the session.

    Order of the rules is the design:
      1. an unusable session label never trades (fail closed);
      2. an exit to zero is UNCONDITIONAL — it ignores the band, the weekday
         AND the same-session guard, because the band is meaningless around a
         target of zero and waiting four days, or even one more session, to
         leave a 3x fund is the failure the vol transform exists to prevent.
         It re-arms harmlessly: once the exit fills, `w_held` is 0 and the
         rule returns (False, 0.0) — UNLESS a reachable book is configured,
         in which case a zero core is a pure-book CONFIGURATION rather than
         an exit, and the cadence below takes over;
      3. one decision per session otherwise, whatever the granularity;
      4. otherwise only the configured weekdays decide;
      5. otherwise a drift of at least `core_rebalance_band` trades, OR the
         trend state differs from the one the last EXECUTED rebalance was
         built in. The band is measured on the CORE weight and a flip moves
         only the REMAINDER, so without the second clause the occupant
         rotation is silently skipped on every decision day whose core drift
         is inside 0.10 — which, by design, is most of them;
      6. a multi-weekday config moves 1/N of the way, not all the way.
    """
    try:
        target = float(w_target)
        held = float(w_held)
    except (TypeError, ValueError):
        return (False, 0.0)
    if not math.isfinite(target) or not math.isfinite(held):
        return (False, held if math.isfinite(held) else 0.0)

    if session_weekday(session_id) < 0:
        return (False, held)

    if target <= 0.0:
        if held > 0.0:
            return (True, 0.0)
        if not _books_configured(cfg):
            return (False, held)
        # A PURE BOOK (`target_vol: 0` or `core_max_weight: 0`): the core is
        # zero by configuration, not by an exit, and the book is the whole
        # position. Falling through to the cadence is what lets it OPEN and
        # ROTATE — the rules below all measure the core weight, and a book
        # whose core is permanently 0 would otherwise read as "already at
        # target, nothing to do" on every session forever.

    if (cache or {}).get(LAST_REBALANCE_KEY) == session_id:
        return (False, held)

    days = rebalance_weekdays(cfg)
    if session_weekday(session_id) not in days:
        return (False, held)

    if target <= 0.0:
        # Only reachable with a book (see above). There is no core weight for
        # the band to measure, so the decision weekday alone decides and
        # `targets_to_orders`'s own `core_band_pct` is what suppresses churn,
        # leg by leg, on a book that has not drifted.
        return (True, 0.0)

    # An ABSENT key is not a rotation: on the first decision of a run there is
    # no executed book to rotate away from, and with the filter off the
    # wrapper never writes the key at all.
    last_state = (cache or {}).get(LAST_STATE_KEY)
    rotated = last_state is not None and _state(last_state) != _state(
        trend_state)

    band = max(0.0, _f(cfg, "core_rebalance_band"))
    if not rotated and round(abs(target - held), Q) < band:
        return (False, held)

    tranches = max(1, len(days))
    return (True, round(held + (target - held) / tranches, Q))
