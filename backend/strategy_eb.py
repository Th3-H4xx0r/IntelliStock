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
"""
from __future__ import annotations

import math

from strategy_x import Q, _finite, _stdev

__all__ = [
    "DEFAULTS", "LAST_REBALANCE_KEY", "eb_core_weight", "eb_should_trade",
    "eb_targets", "rebalance_weekdays", "session_ordinal", "session_weekday",
    "strategy_eb_universe",
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


def eb_core_weight(closes, cfg) -> float | None:
    """Target core weight as a fraction of NAV, or None to REFUSE.

        rv    = max(stdev(ret, 20), stdev(ret, 60)) * sqrt(252)
        w_raw = target_vol / (leverage * rv)
        w     = floor(clamp(w_raw, 0, core_max_weight) / step) * step

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
    if not math.isfinite(w_raw):
        return None
    return _quantize_floor(max(0.0, min(cap, w_raw)), step)


def eb_targets(w, cfg) -> dict:
    """Target weight per symbol as a fraction of NAV. Sums to exactly 1.0.

        core = w
        bil  = (1 - w) * remainder_bil_fraction
        spy  = 1 - core - bil

    `spy` is computed as the RESIDUAL rather than as `(1-w)*(1-dial)` so the
    three legs sum to 1.0 at Q decimals by construction. A weight set summing
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

    dial = max(0.0, min(1.0, _f(cfg, "remainder_bil_fraction")))
    bil = round((1.0 - core) * dial, Q)
    spy = round(1.0 - core - bil, Q)

    targets: dict = {}
    for symbol, weight in ((_s(cfg, "core_symbol"), core),
                           (_s(cfg, "cash_symbol"), bil),
                           (_s(cfg, "off_symbol"), spy)):
        if symbol and weight > 0:
            targets[symbol] = round(targets.get(symbol, 0.0) + weight, Q)
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
    for key in ("reference_symbol", "core_symbol", "off_symbol", "cash_symbol"):
        symbol = _s(cfg, key)
        if symbol and symbol not in out:
            out.append(symbol)
    return out


def eb_should_trade(session_id, w_target, w_held, cfg, cache) -> tuple:
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
         rule returns (False, 0.0);
      3. one decision per session otherwise, whatever the granularity;
      4. otherwise only the configured weekdays decide;
      5. otherwise only a drift of at least `core_rebalance_band` trades;
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
        return (True, 0.0) if held > 0.0 else (False, held)

    if (cache or {}).get(LAST_REBALANCE_KEY) == session_id:
        return (False, held)

    days = rebalance_weekdays(cfg)
    if session_weekday(session_id) not in days:
        return (False, held)

    band = max(0.0, _f(cfg, "core_rebalance_band"))
    if round(abs(target - held), Q) < band:
        return (False, held)

    tranches = max(1, len(days))
    return (True, round(held + (target - held) / tranches, Q))
