"""Strategy X sizing — a leveraged core with a de-lever filter.

Design: docs/superpowers/specs/2026-08-23-strategy-x-design.md

WHAT THE EVIDENCE SAYS
----------------------
This module is what survived an adversarial review of a much larger design. The
original proposal was a five-voter "council" that would choose between TQQQ and
SQQQ by scoring news, macro-LLM output and a Neo4j graph traversal. A
pre-registered offline study (`scripts/strategy_x_voter_study.py`) measured every
proposed voter against non-overlapping 5-day forward QQQ returns and killed it:

    fraction of 5d windows that are UP      0.6045   <- the bar to beat
    above MA200                             0.5827
    trend (the repo's own regime rule)      0.5560
    news_breadth (n=84, CI 0.500-0.705)     0.6071
    vol                                     0.4904
    macro_llm                               0.4762   <- worse than a coin flip

Not one voter beats simply always voting "up". Directional timing of a 3x pair
is not supported by this data, and `~5 false flips a year` converts a +100% year
into 0%.

What DOES survive is a de-lever FILTER, which does not predict direction — it
avoids the convex tail. Measured by replaying THIS MODULE bar by bar over 15.7
years of real closes (`scripts/strategy_x_replay.py`, 2 bps one-way, next-bar
fills, point-in-time filtering):

    config                        CAGR    maxDD   Sharpe   yrs>=100%
    shipped defaults             33.97   -48.50     0.88       4
    tight vol gate (1.2)         28.87   -45.68     0.87       2
    vol gate off                 31.66   -71.08     0.83       3
    bear leg ON (SQQQ)            -4.15  -88.17     0.20       1   <- DEAD
    satellite 20% ON             29.95   -32.74     1.00       1   <- dilutes
    chop = cash not SPY          26.78   -42.27     0.89       1
    TQQQ buy & hold              40.55   -81.66     0.87       5
    SPY buy & hold               14.58   -33.72     0.89       0

99.6x final multiple against SPY's 8.5x, at 4.8 leg changes a year.

NOTE the vectorised study in `scripts/_strategy_x_final.py` reports ~34.7% for
the tight gate, against 28.9% here. The replay is the honest number: it fills on
the NEXT bar rather than the close it just observed, withholds a daily bar until
its session has ended, and groups sessions on the New York calendar. Each of
those is a correctness fix that costs return. Prefer the replay.

THE TWO LEVERS THAT DEFAULT OFF, AND WHY
----------------------------------------
`core_bear_symbol` ("") — the inverse leg. It loses at EVERY filter length
tested (MA50 -15.2%, MA100 -16.1%, MA150 +13.9%, MA200 +6.3%, MA250 +3.8%,
MA300 -6.6% CAGR) and makes drawdown WORSE, not better (-88% vs -38%). Even in
2022, the year it exists for, it returned -38.0% against -43.4% for simply
holding cash. `backend/core_sleeve.py` reached the same verdict independently
("it needed SIX independent suppressors before it stopped losing money"). It is
configurable so it can be re-tested, not because it is expected to work.

`satellite_pct` (0.0) — the stock sleeve. It costs 4.8pp of CAGR and drops
years-above-100% from 3 to 1. `project_conversion-fixed-selection-is-the-gap`
already measured selection losing to a flat SPY on an identical window.

Everything here is pure arithmetic: no clock, no RNG, no I/O. That is
deliberate — `broker.py` is not import-safe (argparse at module scope SystemExits
under pytest), so logic that lives there can only be tested by AST-extracting it
into a stub namespace. This module is imported directly by its tests, the same
reason `core_sleeve.py` exists separately.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from zoneinfo import ZoneInfo

#: Sessions are grouped on the exchange calendar, never on UTC. See
#: `pit_daily_closes` for the measurement that made this necessary.
_NY = ZoneInfo("America/New_York")

__all__ = ["DEFAULTS", "BearSignal", "CoreSignal", "bear_signal",
           "core_signal", "pit_daily_closes", "plan_targets",
           "rank_commodities", "select_satellite", "strategy_x_universe",
           "targets_to_orders"]

#: Quantization grid for every value that crosses a decision boundary. The
#: review's defect 13: quantizing inputs does not quantize the DECISION, and a
#: 1e-12 wobble in a comparand can flip an 80%-of-NAV 3x position. Rounding here
#: does not remove the boundary — hysteresis does that — but it stops bitwise
#: noise from being the thing that moves it.
Q = 6

DEFAULTS = {
    "strategy_x_enabled": False,
    # ── core ──
    "core_bull_symbol": "TQQQ",
    "core_chop_symbol": "SPY",
    "core_bear_symbol": "",          # OFF. See the module docstring.
    "core_weight": 0.90,
    "core_band_pct": 0.05,           # no order inside this drift band
    # ── filter ──
    "core_filter_symbol": "QQQ",     # the tape the filter reads, NOT the traded leg
    "core_filter_ma_bars": 200,      # mid-plateau (150-250 all work), not the peak
    "core_vol_bars": 20,
    # 0 disables. The gate should be LOOSE — it exists to refuse leverage in a
    # genuinely disordered tape, not to trade ordinary chop. Measured through
    # this module over 15.7y (scripts/strategy_x_replay.py), CAGR / maxDD /
    # Sharpe / yrs>=100%:
    #   1.20  28.87 / -45.7 / 0.87 / 2   (8.3 flips/yr — de-levers into recoveries)
    #   2.00  33.37 / -48.7 / 0.87 / 4
    #   2.25  33.97 / -48.5 / 0.88 / 4   <- mid-plateau, shipped
    #   2.50  34.78 / -48.5 / 0.89 / 4
    #   3.00  33.46 / -63.4 / 0.86 / 3   (plateau ends)
    #   off   31.66 / -71.1 / 0.83 / 3
    # 2.0-2.5 is a plateau, so 2.25 is chosen as its middle rather than the
    # 2.50 peak. Tighten toward 1.2 to trade ~5pp of CAGR for a much softer
    # 2022 (-18.2% instead of -36.5%).
    "core_vol_gate_mult": 2.25,
    "core_vol_median_bars": 252,
    "core_vol_median_min_samples": 60,
    # ── bear leg: SQQQ, engaged ONLY on an auto-detected bad regime ──
    # This is NOT the symmetric flip (risk-off -> short), which was measured to
    # turn 99.6x into 0.52x: that version shorts every ordinary pullback and is
    # held through the recoveries. The gate below demands that ALL of a set of
    # crisis conditions agree, sizes the leg smaller than the bull core, and
    # bounds how long it can stay on.
    #
    # `core_bear_min_confirm` is how many of the four must hold:
    #   below the long MA / below the short MA / vol EXPANDING / deep drawdown
    # MEASURED, 15.2y, the broker's own cost model, terminal NAV on $100k:
    #   core_bear_symbol = ""      $6,648,971
    #   core_bear_symbol = "SQQQ"  $4,626,814   <- destroys 30.4% of terminal
    #                                              wealth across just 32 bars
    # And it CANNOT open in a grinding bear: `vol_expand` compares a 20-bar
    # window NESTED inside a 60-bar one, so the ratio is analytically bounded at
    # ~1.76 and a 1.40 threshold demands realised vol roughly DOUBLE against the
    # prior 40 sessions. 2022 (QQQ -33.2%) peaked at 1.31 and opened the gate on
    # 0 of 251 days; 2018Q4 (-23%) likewise 0. Every one of the 32 opens in
    # 15.2 years falls in March-2020 or April-2025 — fast crashes only.
    # Loosening to 3/4 makes 2022 WORSE ($58,258 vs $58,440 with the leg off).
    #
    # So the leg is DEFAULT OFF: it does not cover the common bad market, and it
    # loses money in the rare one it does cover. Kept configurable because the
    # numbers above are the argument, not an opinion.
    "core_bear_weight": 0.35,        # of the core budget, not of NAV
    "core_bear_short_ma_bars": 50,
    # rv(core_vol_bars) / rv(3 x core_vol_bars) = rv20/rv60 at defaults. The
    # windows are NESTED, which is what bounds the ratio near 1.76.
    "core_bear_vol_expansion": 1.40,
    "core_bear_drawdown_pct": 0.15,   # this far below the 252-bar high
    "core_bear_lookback_bars": 252,
    "core_bear_min_confirm": 4,       # 4 = unanimous; 3 = looser
    "core_bear_max_bars": 40,         # hard time limit; decay is -6*sigma^2
    "core_bear_cooldown_bars": 20,    # stay down after the limit, or it cycles
    "core_bear_exit_grace_bars": 2,   # ride out a 1-bar confirm flicker
    # ── satellite (OFF) ──
    "satellite_pct": 0.0,
    "satellite_max_names": 6,
    # BUY/HOLD SPREAD (Novy-Marx & Velikov's sS rule): a name must rank inside
    # `satellite_max_names` to be BOUGHT, but only drops out once it falls
    # outside `satellite_exit_rank`. Stricter to establish a position than to
    # maintain one.
    #
    # This is load-bearing, not a refinement. Without it the sleeve re-draws its
    # whole book every bar — observed live: GBR/FURY/AEHR/MRVL, then
    # CETX/AEHR/CPHI/MRVL, then ATMU/USO/LUNR/CETX on three consecutive days.
    # The conviction score is saturated (3 distinct values over 506,498 trade
    # contexts), so "top N" is mostly a tie broken by ticker spelling and
    # reshuffles daily. The spread is what stops a tie from becoming turnover.
    "satellite_exit_rank": 12,
    # MINIMUM HOLD, in decision bars. The rank band alone cannot stabilise this
    # sleeve because Nexus rotates its whole candidate set daily, so a held name
    # is usually ABSENT from the next bar's ranking rather than lower in it.
    # Cohen & Frazzini: skipping a full week retains 93% of the links alpha, and
    # Ali & Hirshleifer measure the 12-month version netting 41% more than the
    # 1-month for a third of the trading. A hold period is the mechanism that
    # matches a slow signal; daily re-selection is what destroys it.
    "satellite_min_hold_bars": 21,
    # TIEBREAK + LIQUIDITY, both forced by watching this sleeve trade live.
    # The graph score is saturated (3 distinct values over 506,498 contexts), so
    # ties decide the book — and ordering ties by ticker is how bt 331865 came
    # to hold AAL, IDAI, IPDN, PW, the alphabet's first four candidates. Ties
    # now break on trailing momentum over this many daily closes.
    "satellite_momentum_bars": 60,
    # Two of those four were sub-$100M microcaps. At the 45.6bps spread the
    # engine models for microcaps, a $1.40 stock cannot pay for its round trip.
    "satellite_min_price": 5.0,
    # ── commodity sleeve (OFF) ──
    # Holds the top-K commodity ETFs by 60d momentum, among those above their
    # own 100d MA, rebalanced monthly. Funded proportionally out of the core.
    #
    # Measured over 13.7y against a SPY-dilution control at the SAME weight —
    # the control matters, because a sleeve that merely de-levers will improve
    # Sharpe while adding nothing:
    #   15% top2 commodity   CAGR 40.15  maxDD -44.84  ret/DD 0.90  Sharpe 1.09
    #   15% SPY dilution     CAGR 40.50  maxDD -47.77  ret/DD 0.85  Sharpe 1.08
    #   shipped, no sleeve   CAGR 44.46  maxDD -50.07  ret/DD 0.89  Sharpe 1.07
    # So it IS real diversification (~3pp better drawdown at matched return, and
    # the same shape at 10/15/20%, a plateau not a spike) — but it costs 4.3pp
    # of CAGR and halves the years above +100%. That is a risk trade against the
    # stated objective, so it defaults OFF and the operator opts in.
    #
    # top2 beats top3 at every size: concentration is doing work here, because
    # commodities are few and only some of them trend at any one time.
    "commodity_pct": 0.0,
    "commodity_symbols": ["GLD", "SLV", "USO", "UNG", "GDX", "XLE", "DBA", "CPER"],
    "commodity_max_names": 2,
    "commodity_mom_bars": 60,
    "commodity_trend_bars": 100,
    # ── execution ──
    "min_order_usd": 50.0,
    "cost_haircut_pct": 0.006,       # size against the all-in cost, not the mid
}


def _f(cfg, key, default=None):
    if default is None:
        default = DEFAULTS.get(key)
    try:
        v = (cfg or {}).get(key, default)
        if v is None or v == "":
            return float(default)
        out = float(v)
        return out if out == out and abs(out) != float("inf") else float(default)
    except (TypeError, ValueError, AttributeError):
        return float(default)


def _i(cfg, key, default=None):
    if default is None:
        default = DEFAULTS.get(key)
    try:
        v = (cfg or {}).get(key, default)
        if v is None or v == "":
            return int(default)
        return int(v)
    except (TypeError, ValueError, AttributeError):
        return int(default)


def _s(cfg, key, default=None):
    if default is None:
        default = DEFAULTS.get(key, "")
    v = (cfg or {}).get(key, default)
    return str(v if v is not None else default).strip().upper()


def _finite(values):
    """Drop anything that is not a finite positive price.

    A NaN close must never reach a comparison: `nan > x` is False and
    `min(1, nan)` is 1, so bad data reads as "no signal" in one place and "full
    position" in another. Rejecting it here means there is exactly one answer.
    """
    out = []
    for v in values or []:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(f) or f <= 0:
            return None
        out.append(f)
    return out


def _stdev(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _as_utc(value):
    """Timezone-aware UTC, or None. Naive input is treated as UTC rather than
    dropped: the equity backtest clock is naive and the event-time API is aware,
    and silently discarding one of them would blind the filter on half the runs.
    """
    from datetime import datetime as _dt, timezone as _tz

    if isinstance(value, _dt):
        return value if value.tzinfo else value.replace(tzinfo=_tz.utc)
    try:
        parsed = _dt.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_tz.utc)


def pit_daily_closes(bars, as_of) -> list:
    """Daily closes visible at `as_of`, oldest first.

    THE point-in-time boundary for this strategy. A bar stamped later than the
    decision time is not knowable at the decision, and at the 15m/1h cadence
    these backtests actually run, "today's daily bar" IS that session's 16:00
    close — roughly six hours in the future of a 09:45 decision. Comparing on
    date alone is the single most common lookahead in this codebase, so this
    compares on the full timestamp.

    Each day's close is its LAST visible bar, so a partial session contributes
    the most recent price rather than being dropped or completed with future
    data. Pure: no clock read, no I/O.

    A bar's timestamp is its OPEN; its close is knowable only at the end of the
    interval it covers. For INTRADAY bars the stamp is within the session and
    `ts <= as_of` is the right test. For DAILY bars it is not: the stamp is
    midnight or the session open while `c` is the 16:00 close, so `ts <= as_of`
    would hand the strategy today's close at a 09:45 decision — precisely the
    lookahead this function exists to prevent.

    So the cadence is inferred from the smallest positive gap between stamps,
    and a daily-or-coarser series is required to be from a STRICTLY EARLIER day.
    Smallest gap rather than median because weekend gaps would otherwise make an
    hourly series look multi-day and discard real bars.
    """
    cutoff = _as_utc(as_of)
    if cutoff is None or not bars:
        return []

    stamps = []
    for bar in bars:
        if isinstance(bar, dict):
            ts = _as_utc(bar.get("t") or bar.get("timestamp") or bar.get("date"))
            if ts is not None:
                stamps.append(ts)
    if not stamps:
        return []
    stamps.sort()
    gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])
            if (b - a).total_seconds() > 0]
    daily = bool(gaps) and min(gaps) >= 23 * 3600

    by_day: dict = {}
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        ts = _as_utc(bar.get("t") or bar.get("timestamp") or bar.get("date"))
        if ts is None:
            continue
        if daily:
            # A DAILY bar's stamp is a date LABEL, not a moment — Alpaca stamps
            # 1Day bars at 05:00Z and yfinance at 00:00, both meaning "that
            # session". Converting a label to NY would shift it back a day.
            # Compare the label against the decision's NY session date.
            if ts.date() >= cutoff.astimezone(_NY).date():
                continue
        elif ts > cutoff:
            continue
        close = bar.get("c", bar.get("close"))
        try:
            close = float(close)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(close) or close <= 0:
            continue
        # INTRADAY bars group on the NEW YORK session date, not UTC. Under EST a
        # session's 19:00-20:00 ET tail lands on the next UTC date, so Friday's
        # after-hours print becomes a phantom Saturday "close": measured, a
        # 365-day window yields 313 UTC-grouped closes against 261 real weekdays
        # (+19.9%), while the same window under EDT yields exactly 261. That
        # makes MA200 span ~167 real sessions in winter and 200 in summer — a
        # lookback that drifts with the season. Daily bars keep their label.
        day = (ts.date() if daily else ts.astimezone(_NY).date()).isoformat()
        prior = by_day.get(day)
        # Ties on timestamp resolve to the later-listed bar deterministically;
        # `>=` keeps the last one in a stable input order.
        if prior is None or ts >= prior[0]:
            by_day[day] = (ts, close)
    return [by_day[d][1] for d in sorted(by_day)]


@dataclass(frozen=True)
class CoreSignal:
    """Why the core is or is not levered this bar. `reason` is not decoration:
    the sleeve's recurring failure mode in this repo has been silence — an
    inert lever that logged nothing looked identical to a working one."""

    risk_on: bool
    reason: str
    price: float = 0.0
    ma: float = 0.0
    rvol: float = 0.0
    rvol_median: float = 0.0


def core_signal(closes, config) -> CoreSignal:
    """Risk-on/off from a close series, oldest first.

    Two gates, both required:
      1. trend  — price above its `core_filter_ma_bars` moving average;
      2. vol    — 20-bar realised vol below `core_vol_gate_mult` x its own
                  trailing median. This one is what takes max drawdown from
                  -58.9% to -38.4%, at a cost of ~1pp of CAGR.

    Fails CLOSED: insufficient history, a non-finite close, or a non-positive
    MA all return risk_on=False. A cold start must never read as risk-on.
    """
    cfg = config or {}
    ma_bars = max(2, _i(cfg, "core_filter_ma_bars"))
    vol_bars = max(2, _i(cfg, "core_vol_bars"))

    px = _finite(closes)
    if px is None:
        return CoreSignal(False, "non-finite close in the series")
    if len(px) < ma_bars:
        return CoreSignal(False,
                          f"insufficient history: {len(px)} closes < {ma_bars}")

    price = round(px[-1], Q)
    ma = round(sum(px[-ma_bars:]) / ma_bars, Q)
    if ma <= 0:
        return CoreSignal(False, "non-positive moving average")
    if price <= ma:
        return CoreSignal(False,
                          f"price {price:.4f} below MA{ma_bars} {ma:.4f}",
                          price, ma)

    gate = _f(cfg, "core_vol_gate_mult")
    if gate <= 0:
        return CoreSignal(True, f"price {price:.4f} above MA{ma_bars} {ma:.4f} "
                                "(vol gate off)", price, ma)

    rets = [px[i] / px[i - 1] - 1.0 for i in range(1, len(px))]
    if len(rets) < vol_bars:
        return CoreSignal(False, "insufficient history for the vol gate",
                          price, ma)
    ann = math.sqrt(252.0)
    series = [round(_stdev(rets[i - vol_bars:i]) * ann, Q)
              for i in range(vol_bars, len(rets) + 1)]
    med_bars = max(1, _i(cfg, "core_vol_median_bars"))
    window = series[-med_bars:]
    if len(window) < max(1, _i(cfg, "core_vol_median_min_samples")):
        # Not enough samples to know what "normal" vol is. Trend alone decides;
        # inventing a median here would gate on noise.
        return CoreSignal(True, f"price above MA{ma_bars} "
                                "(vol median sample too short)", price, ma)
    ordered = sorted(window)
    mid = len(ordered) // 2
    median = (ordered[mid] if len(ordered) % 2
              else (ordered[mid - 1] + ordered[mid]) / 2.0)
    rvol = series[-1]
    median = round(median, Q)
    limit = round(median * gate, Q)
    # No `median > 0` guard. A zero median is not a benign "gate unavailable"
    # state: it happens when most of the window is a flat/halted tape (a
    # last-good backfill, a data gap), and short-circuiting on it forced
    # risk-ON at 212% realised vol in testing, with the audit string cheerfully
    # asserting "rvol 2.1173 <= 0.0000". The guard could only ever flip the
    # answer toward risk-on; when both are zero the comparison is already False.
    if rvol > limit:
        return CoreSignal(False,
                          f"vol gate: rvol {rvol:.4f} > {gate:.2f}x median "
                          f"{median:.4f}", price, ma, rvol, median)
    return CoreSignal(True,
                      f"price {price:.4f} above MA{ma_bars} {ma:.4f}, "
                      f"rvol {rvol:.4f} <= {limit:.4f}",
                      price, ma, rvol, median)


@dataclass(frozen=True)
class BearSignal:
    """Whether the tape is bad enough to hold an inverse leg, and why."""

    engaged: bool
    reason: str
    confirms: int = 0
    detail: tuple = ()


def bear_signal(closes, config, bars_engaged: int = 0) -> BearSignal:
    """Auto-detect a genuinely bad regime — not merely 'not risk-on'.

    Four independent conditions, each answering a different question:

        below_long   price under its 200-bar MA        is the trend broken?
        below_short  price under its 50-bar MA         is it broken NOW?
        vol_expand   rv20 / rv60 above the threshold   is it disorderly?
        deep_dd      >= X% below the 252-bar high      has it actually fallen?

    KNOWN LIMIT, measured: `vol_expand` compares a short window NESTED inside
    the long one, so the ratio is analytically bounded near 1.76 and the default
    1.40 demands realised vol roughly double against the prior 40 sessions. That
    is a CRASH signature, not a BEAR signature — 2022 (-33.2%) never exceeded
    1.31 and the gate opened on 0 of 251 days. With `core_bear_min_confirm=4`
    this condition is a hard veto on the other three. Treat this detector as
    "fast crash", not "bad market".

    `core_bear_min_confirm` of them must hold. Requiring several is the whole
    point: any ONE of these fires on an ordinary pullback, and shorting ordinary
    pullbacks is what made the symmetric version lose 99% of its value. Depth
    plus disorder plus a broken trend is a different state from "off its highs".

    `bars_engaged` enforces the time limit. A -3x inverse fund carries a
    -6*sigma^2 drag, so time is the enemy even when the direction is right:
    past `core_bear_max_bars` the leg stands down regardless of the tape.

    Pure — the caller owns the bar counter. Fails CLOSED: insufficient history
    or a bad close means NOT engaged, because the expensive error here is
    holding a leveraged short by accident.
    """
    cfg = config or {}
    if not _s(cfg, "core_bear_symbol"):
        return BearSignal(False, "bear leg disabled (no core_bear_symbol)")

    long_bars = max(2, _i(cfg, "core_filter_ma_bars"))
    short_bars = max(2, _i(cfg, "core_bear_short_ma_bars"))
    look = max(2, _i(cfg, "core_bear_lookback_bars"))
    vol_bars = max(2, _i(cfg, "core_vol_bars"))
    need = max(1, _i(cfg, "core_bear_min_confirm"))

    max_bars = _i(cfg, "core_bear_max_bars")
    if max_bars > 0 and int(bars_engaged or 0) >= max_bars:
        return BearSignal(False, f"time limit: {bars_engaged} bars >= "
                                 f"{max_bars}, standing down")

    px = _finite(closes)
    if px is None or len(px) < max(long_bars, look, vol_bars * 4) + 1:
        return BearSignal(False, "insufficient history for the bear gate")

    price = round(px[-1], Q)
    ma_long = round(sum(px[-long_bars:]) / long_bars, Q)
    ma_short = round(sum(px[-short_bars:]) / short_bars, Q)
    hi = max(px[-look:])
    off_high = round((hi - price) / hi, Q) if hi > 0 else 0.0

    rets = [px[i] / px[i - 1] - 1.0 for i in range(1, len(px))]
    ann = math.sqrt(252.0)
    rv_short = round(_stdev(rets[-vol_bars:]) * ann, Q)
    rv_long = round(_stdev(rets[-vol_bars * 3:]) * ann, Q)
    ratio = round(rv_short / rv_long, Q) if rv_long > 0 else 0.0

    checks = (
        ("below_long", price < ma_long),
        ("below_short", price < ma_short),
        ("vol_expand", ratio > _f(cfg, "core_bear_vol_expansion")),
        ("deep_dd", off_high >= _f(cfg, "core_bear_drawdown_pct")),
    )
    hits = [n for n, ok in checks if ok]
    if len(hits) < need:
        return BearSignal(False,
                          f"bear gate {len(hits)}/{need}: {'+'.join(hits) or 'none'}",
                          len(hits), tuple(n for n, _ in checks))
    return BearSignal(True,
                      f"BEAR REGIME {len(hits)}/{need}: {'+'.join(hits)} "
                      f"(off_high {off_high:.1%}, rv {ratio:.2f}x, "
                      f"bar {bars_engaged})",
                      len(hits), tuple(hits))


def rank_commodities(closes_by_symbol: dict, config) -> list:
    """Commodity ETFs worth holding, best first. Pure.

    Two gates, both required: the ETF must be in its OWN uptrend (above its
    `commodity_trend_bars` MA) and it is then ranked by `commodity_mom_bars`
    return. The uptrend gate is what lets the sleeve hold NOTHING — commodities
    spend long stretches with none of them trending, and a sleeve that must
    always be full would be forced into whichever one is falling least.

    Deterministic: ties break on symbol, never on dict order.
    """
    cfg = config or {}
    mom_bars = max(2, _i(cfg, "commodity_mom_bars"))
    trend_bars = max(2, _i(cfg, "commodity_trend_bars"))
    scored = []
    for sym in sorted(closes_by_symbol or {}):
        px = _finite(closes_by_symbol.get(sym))
        if not px or len(px) < max(mom_bars, trend_bars) + 1:
            continue
        ma = sum(px[-trend_bars:]) / trend_bars
        if ma <= 0 or px[-1] <= ma:
            continue                      # not in its own uptrend
        prior = px[-1 - mom_bars]
        if prior <= 0:
            continue
        scored.append((round(px[-1] / prior - 1.0, Q), sym))
    # Highest momentum first; symbol ascending as the tie-break.
    scored.sort(key=lambda kv: (-kv[0], kv[1]))
    return [s for _, s in scored[:max(0, _i(cfg, "commodity_max_names"))]]


def strategy_x_universe(config) -> list:
    """Every symbol this strategy can trade or read, deterministic order.

    The strategy owns its universe rather than depending on the instance's
    watchlist — the same thing the residual sleeve does via
    `_residual_sleeve_universe_symbols`. Without this the filter symbol has no
    bars and the traded legs have no price, and BOTH failures are silent: the
    strategy simply emits nothing.
    """
    cfg = config or {}
    syms = [_s(cfg, k) for k in ("core_filter_symbol", "core_bull_symbol",
                                 "core_chop_symbol", "core_bear_symbol")]
    if _f(cfg, "commodity_pct") > 0:
        syms += [str(s).strip().upper()
                 for s in (cfg.get("commodity_symbols") or []) if s]
    seen, out = set(), []
    for s in syms:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def select_satellite(ranked, held, config, ages=None) -> list:
    """Satellite membership with a buy/hold spread AND a minimum hold. Pure.

    Two mechanisms, and the second one is the load-bearing half here:

    1. RANK BAND — a HELD name survives while inside `satellite_exit_rank`; a
       NEW name must be inside `satellite_max_names`.
    2. MINIMUM HOLD — a name younger than `satellite_min_hold_bars` is kept
       REGARDLESS of whether it is ranked at all.

    The band alone does not work against this signal, measured: Nexus discovers
    a different candidate set every bar, so yesterday's picks are not merely
    lower-ranked today, they are ABSENT. Survivors is then always empty and the
    book re-draws daily — observed live as AAL/PW/IPDN/IDAI -> CAR/AAOI/CPHI/AAL
    -> ATMU/LUNR on consecutive bars. A rank band cannot stabilise a rotating
    universe; only a hold period can.

    `ages` maps symbol -> bars held. Missing means new.
    """
    cfg = config or {}
    keep_n = max(0, _i(cfg, "satellite_max_names"))
    exit_n = max(keep_n, _i(cfg, "satellite_exit_rank"))
    min_hold = max(0, _i(cfg, "satellite_min_hold_bars"))
    names = [str(s).strip().upper() for s in (ranked or []) if s]
    names = list(dict.fromkeys(names))
    holding = {str(s).strip().upper() for s in (held or set())}
    ages = {str(k).strip().upper(): int(v or 0) for k, v in (ages or {}).items()}

    # Young holdings are kept whether or not they rank today. Sorted by age
    # (oldest first) so the set is deterministic and the oldest retire first.
    #
    # `s in ages` is required, not just an age lookup: a holding with NO age
    # record is not one this sleeve is tracking (a cleared cache, or another
    # strategy's position), and granting it a minimum hold would freeze
    # whatever happened to be in the book. Untracked names fall through to the
    # ordinary rank band.
    young = sorted((s for s in holding
                    if s in ages and ages[s] < min_hold),
                   key=lambda s: (-ages[s], s))[:keep_n]
    survivors = [s for s in names[:exit_n] if s in holding and s not in young]
    keep = young + survivors
    room = max(0, keep_n - len(keep))
    adds = [s for s in names[:keep_n] if s not in holding][:room]
    return keep + adds


def plan_targets(*, risk_on: bool, config, satellite_ranked=None,
                 held_core: str = "", commodity_ranked=None,
                 bear_engaged: bool = False) -> tuple[dict, list]:
    """Target weight per symbol as a fraction of NAV, plus why.

    The chop occupant is a RESIDUAL, exactly as in `index_core_tilt.plan_targets`
    — whatever the levered core and the satellite do not use goes there rather
    than to cash. Measured: routing to cash instead costs 8pp of CAGR.
    """
    cfg = config or {}
    notes: list[str] = []
    targets: dict[str, float] = {}

    bull = _s(cfg, "core_bull_symbol")
    chop = _s(cfg, "core_chop_symbol")
    bear = _s(cfg, "core_bear_symbol")
    weight = max(0.0, min(1.0, _f(cfg, "core_weight")))

    # ── satellite first; the core takes what is left ──
    sat_pct = max(0.0, min(1.0, _f(cfg, "satellite_pct")))
    names = [str(s).strip().upper() for s in (satellite_ranked or []) if s]
    # Exclude the core legs AND the commodity candidates. Observed live: the
    # satellite ranked USO into the stock sleeve while the commodity sleeve was
    # also holding energy, concentrating 25% of NAV into one sector by accident.
    # The two sleeves must not compete for the same names.
    _com_syms = {str(s).strip().upper()
                 for s in (cfg.get("commodity_symbols") or []) if s}
    names = [s for s in names if s not in (bull, chop, bear)
             and s not in _com_syms]
    # Dedupe, preserving rank order. A repeated name is written once but was
    # charged to the budget once per occurrence, silently leaving the difference
    # in cash.
    names = list(dict.fromkeys(names))
    if sat_pct > 0 and names:
        # Floor to the grid, never round. Rounding each share UP breaches the
        # budget (0.5/3 rounded to 6dp three times is 0.500001), and a weight
        # set that sums past 1.0 asks for a clip the account cannot fund.
        scale = 10 ** Q
        each = math.floor(sat_pct / len(names) * scale) / scale
        for s in names:
            targets[s] = each
        sat_pct = round(each * len(names), Q)
        notes.append(f"satellite {sat_pct:.0%} across {len(names)} name(s)")
    elif sat_pct > 0:
        # A dead ranking degrades to the index, never to cash.
        notes.append("no satellite names ranked — core absorbs the sleeve")
        sat_pct = 0.0

    # ── commodity sleeve, funded out of the core budget ──
    com_pct = max(0.0, min(1.0, _f(cfg, "commodity_pct")))
    com = [str(s).strip().upper() for s in (commodity_ranked or []) if s]
    com = [s for s in com if s not in (bull, chop, bear) and s not in targets]
    com = list(dict.fromkeys(com))[:max(0, _i(cfg, "commodity_max_names"))]
    if com_pct > 0 and com:
        scale = 10 ** Q
        each = math.floor(com_pct / len(com) * scale) / scale
        for s in com:
            targets[s] = each
        com_pct = round(each * len(com), Q)
        notes.append(f"commodity {com_pct:.0%} across {len(com)}: {', '.join(com)}")
    elif com_pct > 0:
        # No commodity is in its own uptrend. Holding none is the correct
        # answer; the budget returns to the core rather than forcing a pick.
        notes.append("no commodity in an uptrend — core absorbs the sleeve")
        com_pct = 0.0

    core_budget = round(max(0.0, 1.0 - sat_pct - com_pct), Q)

    if risk_on:
        targets[bull] = round(core_budget * weight, Q)
        rest = round(core_budget - targets[bull], Q)
        if rest > 0:
            targets[chop] = round(targets.get(chop, 0.0) + rest, Q)
        notes.append(f"risk-on: {targets[bull]:.1%} {bull}")
        return targets, notes

    # ── risk-off ──
    # The bear leg engages ONLY when the crisis gate says the regime is bad, not
    # merely because the bull filter is off. Risk-off is common; a bad regime is
    # rare. Shorting every risk-off bar is the symmetric flip that lost 99% of
    # its value.
    if bear and bear_engaged:
        # Never flip a levered long straight into a levered short: that is two
        # 3x round trips at full core size in one bar, the most expensive trade
        # the system can make. One bar in the un-levered occupant first.
        if held_core and held_core.strip().upper() == bull:
            targets[chop] = round(targets.get(chop, 0.0) + core_budget, Q)
            notes.append(f"bear regime: direct {bull}->{bear} flip blocked, "
                         f"routing via {chop} for one bar")
            return targets, notes
        # Sized by core_bear_weight, NOT core_weight: a -3x inverse carries a
        # -6*sigma^2 drag against the long leg's -3*sigma^2, so the short side
        # is deliberately the smaller position.
        bear_w = max(0.0, min(1.0, _f(cfg, "core_bear_weight")))
        targets[bear] = round(core_budget * bear_w, Q)
        rest = round(core_budget - targets[bear], Q)
        if rest > 0:
            targets[chop] = round(targets.get(chop, 0.0) + rest, Q)
        notes.append(f"BEAR REGIME: {targets[bear]:.1%} {bear}, "
                     f"{rest:.1%} {chop}")
        return targets, notes

    targets[chop] = round(targets.get(chop, 0.0) + core_budget, Q)
    notes.append(f"risk-off: {targets[chop]:.1%} {chop}")
    return targets, notes


def targets_to_orders(targets: dict, *, nav: float, positions: dict,
                      prices: dict, cash: float, config,
                      owned: set | None = None) -> tuple[dict, dict]:
    """Turn target weights into `{sym: 1|0|-1}` plus `_nexus_position_sizes`.

    Sizing MUST be published explicitly. A bare `1` is sized by the broker's
    default `cash_per_trade` (~$1,000), which is how `index_core_tilt` asked for
    $6,000 of SPY and received $900.
    """
    cfg = config or {}
    decisions: dict[str, int] = {}
    sizes: dict[str, dict] = {}
    nav = float(nav or 0.0)
    if nav <= 0:
        return decisions, sizes

    band = max(0.0, _f(cfg, "core_band_pct"))
    min_usd = max(0.0, _f(cfg, "min_order_usd"))
    haircut = max(0.0, min(0.5, _f(cfg, "cost_haircut_pct")))
    cash = max(0.0, float(cash or 0.0))

    want = {str(s).strip().upper(): float(w or 0.0)
            for s, w in (targets or {}).items()}
    # Only ever sell what THIS strategy owns. Walking the whole book means that
    # co-deployed with any other position-taking strategy, every one of its
    # holdings is outside `targets`, gets a -1 with sell_fraction 1.0, and lands
    # in `_nexus_sell_enforcement` — which is a HARD override in the broker, not
    # advisory. That liquidates the other strategy's book on every tick.
    scope = {str(s).strip().upper() for s in (owned or set())} | set(want)
    held = {str(s).strip().upper(): float(q or 0.0)
            for s, q in (positions or {}).items()
            if q and str(s).strip().upper() in scope}

    # Sells first: proceeds fund the buys, and a held name absent from the plan
    # is a full exit. Sorted so the emitted order is deterministic.
    proceeds = 0.0
    for sym in sorted(held):
        px = float((prices or {}).get(sym) or 0.0)
        current = held[sym] * px
        target_usd = want.get(sym, 0.0) * nav
        if px <= 0 or current <= 0:
            continue
        # The band is a no-churn rule around a target you INTEND to hold. It is
        # meaningless around a target of zero: applying it there means a small
        # unwanted holding is never sold — not late, NEVER — and with cash at 0
        # the whole plan deadlocks, because the stranded weight is exactly what
        # the intended buy needed. Exits are unconditional.
        if target_usd > 0 and round(abs(target_usd - current) / nav, Q) <= band:
            continue
        if target_usd < current:
            delta = current - target_usd
            if delta < min_usd and target_usd > 0:
                continue
            decisions[sym] = -1
            sizes[sym] = {"sell_fraction": max(0.0, min(1.0, delta / current))}
            proceeds += delta

    # Buys, largest intended weight first. Alphabetical order rationed cash by
    # ticker string, so a 10% satellite could starve the 90% core purely because
    # "AAPL" sorts before "TQQQ". Symbol is the tie-break so this stays
    # deterministic.
    for sym in sorted(want, key=lambda s: (-want[s], s)):
        px = float((prices or {}).get(sym) or 0.0)
        if px <= 0 or sym in decisions:
            continue
        current = held.get(sym, 0.0) * px
        delta = want[sym] * nav - current
        if round(abs(delta) / nav, Q) <= band:
            continue
        if delta <= 0 or delta < min_usd:
            continue
        # Size off SETTLED cash only. Counting this bar's sell proceeds looks
        # right — the sells are in the same payload — but the equity lane only
        # QUEUES them: `NextEventExecutionSimulator` fills next bar, and the
        # broker sizes every buy from `portfolio_emulator.get_cash()`, which
        # still excludes the pending sell. Asking for more than settled cash
        # does not borrow against the sell, it gets CLIPPED: measured, a
        # $29,863 SQQQ buy became $500 and a $90,930 SPY buy became $60,
        # leaving the book ~90% in cash for a full session on 64 bars.
        #
        # The cost of being correct here is one bar of cash on a flip. That is
        # real, bounded, and much cheaper than a stub position. The broker has
        # its own credit mechanism for this (`backtest_credit_pending_sell_
        # proceeds` / `buy_ceiling`); routing through that is the supported way
        # to close the gap, not guessing at the balance from in here.
        spend = min(delta, cash * (1.0 - haircut))
        if spend < min_usd:
            continue
        decisions[sym] = 1
        sizes[sym] = {"buy_cash": round(spend, 2)}
        cash = max(0.0, cash - spend)

    return decisions, sizes
