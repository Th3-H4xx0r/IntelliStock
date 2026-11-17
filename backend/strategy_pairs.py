"""Strategy Pairs (laggard rotation): the pure rule, no broker, no I/O.

The claim under test (Instagram reel, 2026-11-17): "buy the stock that fell
behind its twin and short the one that ran ahead" (Coke/Pepsi, Walmart/Target,
Home Depot/Lowe's ...).

The engine is long-only, so the short leg cannot be placed. The rule is tested
as a long-only TILT inside each pair instead, which carries the same signal:

  each pair gets 1/N of NAV; inside a pair with signal s in {-1, 0, +1}
      weight(A) = slot * (0.5 + 0.5 * s * tilt)
      weight(B) = slot * (0.5 - 0.5 * s * tilt)

  s = +1 holds the whole slot in A, s = -1 in B, s = 0 splits it 50/50.

The return of that book minus the return of the 50/50 book (mode "hold", the
control run) is 0.5 * s * (rA - rB): exactly half of the dollar-neutral
long/short pair return. So "does the laggard snap back" is measured on the
engine as candidate run minus control run, same pairs, same window, same costs.

Modes:
  hold      s = 0 always. The control.
  reversal  s points at the leg with the LOWER trailing `lookback_bars` return.
  momentum  the mirror image (the reel's "-99%" line): s points at the leader.
  zscore    z of log(A/B) over `z_window` sessions; enter the cheap leg when
            |z| > z_entry, go back to 50/50 when z crosses `z_exit`.

Offline evidence (.claude/handoffs/pairs-reversal-2026-11-17/): net of 5 bps
the 12-pair basket made about 0.7%/yr from 2013; the daily flip loses money.
"""
from __future__ import annotations

import math

Q = 10  # rounding places for byte-stable arithmetic across platforms

MODES = ("hold", "reversal", "momentum", "zscore")

#: The twelve pairs the reel shows on screen.
REEL_PAIRS = [
    ["KO", "PEP"], ["WMT", "TGT"], ["HD", "LOW"], ["V", "MA"],
    ["XOM", "CVX"], ["UPS", "FDX"], ["F", "GM"], ["JPM", "BAC"],
    ["MCD", "YUM"], ["DAL", "UAL"], ["LMT", "NOC"], ["NKE", "UAA"],
]

DEFAULTS = {
    "strategy_pairs_enabled": False,
    "pairs": REEL_PAIRS,
    "mode": "reversal",
    # ── reversal / momentum ──
    "lookback_bars": 5,
    # ── zscore ──
    "z_window": 60,
    "z_entry": 2.0,
    "z_exit": 0.0,
    # 1.0 rotates the whole pair slot into one leg; 0.5 would hold 75/25.
    "tilt": 1.0,
    # ── order plumbing (read by strategy_x.targets_to_orders) ──
    # A fraction of NAV. With 24 legs at ~4% each, 1% keeps the 50/50 control
    # from trading on ordinary drift while never suppressing a rotation
    # (an exit to zero is unconditional, an open from flat ignores the band).
    "core_band_pct": 0.01,
    "min_order_usd": 25.0,
    "cost_haircut_pct": 0.006,
    # A single-pair run holds up to 100% of NAV in one stock; the broker's 15%
    # failsafe would trim that buy to nothing. Honoured only with BOTH keys.
    "broker_max_single_position_pct": 1.0,
    "honour_single_position_cap": True,
    # Let a rotation's buy be sized against the same bar's funding sell.
    # Without it every flip spends a session in cash, which is not the rule.
    "backtest_credit_pending_sell_proceeds": True,
}


def _f(cfg, key, default=None):
    """Float from config; anything unparseable or non-finite -> DEFAULTS[key]."""
    fallback = DEFAULTS.get(key) if default is None else default
    try:
        value = float((cfg or {}).get(key, fallback))
    except (TypeError, ValueError):
        return float(fallback)
    if not math.isfinite(value):
        return float(fallback)
    return value


def _i(cfg, key, minimum=1):
    """Positive int from config; anything else -> DEFAULTS[key]."""
    try:
        value = int(float((cfg or {}).get(key, DEFAULTS[key])))
    except (TypeError, ValueError):
        value = int(DEFAULTS[key])
    return value if value >= minimum else int(DEFAULTS[key])


def pairs_mode(cfg) -> str:
    """The configured mode. An unknown mode is `hold`: never trade on a typo."""
    mode = str((cfg or {}).get("mode", DEFAULTS["mode"]) or "").strip().lower()
    return mode if mode in MODES else "hold"


def pairs_of(cfg) -> list:
    """`[(A, B), ...]` upper-cased, malformed and self-pairs dropped, deduped."""
    raw = (cfg or {}).get("pairs", DEFAULTS["pairs"])
    out, seen = [], set()
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        a, b = (str(x or "").strip().upper() for x in item)
        if not a or not b or a == b or (a, b) in seen or (b, a) in seen:
            continue
        seen.add((a, b))
        out.append((a, b))
    return out


def strategy_pairs_universe(cfg) -> list:
    """Every leg the lane can hold, sorted."""
    return sorted({leg for pair in pairs_of(cfg) for leg in pair})


def aligned_closes(obs_a, obs_b) -> tuple:
    """Closes of A and B on the sessions BOTH have, oldest first.

    `obs_*` are `(session_id, close)` pairs. A session one leg is missing is
    dropped for both, so a return is always measured over the same days.
    """
    b_by_session = {s: c for s, c in (obs_b or [])}
    closes_a, closes_b = [], []
    for session, close in (obs_a or []):
        other = b_by_session.get(session)
        if other is None:
            continue
        closes_a.append(float(close))
        closes_b.append(float(other))
    return closes_a, closes_b


def _ok(values) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0
               for v in values)


def reversal_signal(closes_a, closes_b, lookback) -> int:
    """+1 when A lagged B over the last `lookback` sessions, -1 when B lagged.

    0 on a tie, short history, or any close that is not finite and positive.
    """
    lookback = int(lookback)
    if lookback < 1 or len(closes_a) <= lookback or len(closes_b) <= lookback:
        return 0
    ends = (closes_a[-1], closes_a[-1 - lookback],
            closes_b[-1], closes_b[-1 - lookback])
    if not _ok(ends):
        return 0
    ret_a = round(ends[0] / ends[1] - 1.0, Q)
    ret_b = round(ends[2] / ends[3] - 1.0, Q)
    if ret_a < ret_b:
        return 1
    if ret_b < ret_a:
        return -1
    return 0


def spread_z(closes_a, closes_b, window):
    """z-score of today's log(A/B) against its last `window` sessions, or None.

    Sample standard deviation (n - 1), the same number pandas' rolling std
    returns, so the offline study and the engine agree on every entry.
    """
    window = int(window)
    if window < 3 or len(closes_a) < window or len(closes_b) < window:
        return None
    tail_a, tail_b = closes_a[-window:], closes_b[-window:]
    if not _ok(tail_a) or not _ok(tail_b):
        return None
    spread = [math.log(a / b) for a, b in zip(tail_a, tail_b)]
    mean = sum(spread) / window
    var = sum((x - mean) ** 2 for x in spread) / (window - 1)
    if var <= 0:
        return None
    return round((spread[-1] - mean) / math.sqrt(var), Q)


def zscore_signal(z, prev, entry, exit_level) -> int:
    """Next state of one pair's z-score machine.

    z > entry  means A is rich against B: hold B (-1).
    z < -entry means A is cheap:           hold A (+1).
    An open position closes when z comes back through `exit_level`; it does not
    re-enter on the same session. `z is None` keeps the previous state: a
    session that cannot be measured is not a reason to trade.
    """
    prev = prev if prev in (-1, 0, 1) else 0
    if z is None:
        return prev
    if prev == 0:
        if z > entry:
            return -1
        if z < -entry:
            return 1
        return 0
    if prev == -1 and z <= exit_level:
        return 0
    if prev == 1 and z >= -exit_level:
        return 0
    return prev


def pair_signal(closes_a, closes_b, prev, cfg) -> int:
    """The signal for one pair under the configured mode."""
    mode = pairs_mode(cfg)
    if mode == "hold":
        return 0
    if mode == "zscore":
        z = spread_z(closes_a, closes_b, _i(cfg, "z_window", minimum=3))
        return zscore_signal(z, prev, _f(cfg, "z_entry"), _f(cfg, "z_exit"))
    signal = reversal_signal(closes_a, closes_b, _i(cfg, "lookback_bars"))
    return -signal if mode == "momentum" else signal


def pairs_targets(signals, cfg) -> dict:
    """Target weights `{symbol: weight}` for the whole book. Sums to 1.0.

    `signals` maps `(A, B)` to -1 / 0 / +1; a pair with no entry is held 50/50.
    A symbol that sits in two pairs gets the sum of its two slots.
    """
    pairs = pairs_of(cfg)
    if not pairs:
        return {}
    slot = 1.0 / len(pairs)
    tilt = max(0.0, min(1.0, _f(cfg, "tilt")))
    targets: dict = {}
    for a, b in pairs:
        s = signals.get((a, b), 0)
        s = s if s in (-1, 0, 1) else 0
        targets[a] = targets.get(a, 0.0) + slot * (0.5 + 0.5 * s * tilt)
        targets[b] = targets.get(b, 0.0) + slot * (0.5 - 0.5 * s * tilt)
    return {sym: round(w, Q) for sym, w in targets.items() if w > 0}
